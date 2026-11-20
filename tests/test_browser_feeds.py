import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import feedparser
from playwright.sync_api import sync_playwright

from scripts import update_feeds as updater


USER = '<shreddit-app user-logged-in="true"></shreddit-app>'
POST = """<shreddit-post permalink="/r/test/comments/abc/hello/" author="writer"
post-title="Hello" created-timestamp="2026-11-19T08:00:00.000+0000"
content-href="https://example.org/"><div slot="text-body">Test body</div></shreddit-post>"""


class BrowserFeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pw = sync_playwright().start()
        cls.addClassCleanup(pw.stop)
        executable = pw.chromium.executable_path
        if not Path(executable).exists():
            executable = shutil.which("chromium") or shutil.which("google-chrome")
        if not executable:
            raise unittest.SkipTest("Chromium is required for listing tests")
        cls.browser = pw.chromium.launch(executable_path=executable)
        cls.addClassCleanup(cls.browser.close)

    def scrape(self, user, posts):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        html = user + '<div id="siteTable">' + posts + '</div>'
        page.route("**/*", lambda route: route.fulfill(content_type="text/html", body=html))
        return updater.scrape_listing(page, "test")

    def test_signed_in_listing_renders_canonical_posts_only(self):
        posts = POST + POST + POST.replace("/r/test/", "/r/other/") + POST.replace(
            '<shreddit-post ', '<shreddit-post promoted ').replace("abc", "def")
        data = self.scrape(USER, posts)
        parsed = feedparser.parse(updater.render_feed(data, "", "test"))
        self.assertFalse(parsed.bozo)
        self.assertEqual(parsed.feed.id, "https://old.reddit.com/r/test/.rss")
        self.assertEqual(len(parsed.entries), 1)
        entry = parsed.entries[0]
        self.assertEqual(entry.id, "https://www.reddit.com/r/test/comments/abc/hello/")
        self.assertEqual(entry.title, "Hello")
        self.assertEqual(entry.author, "/u/writer")
        self.assertIn("https://example.org/", entry.content[0].value)
        self.assertEqual(entry.published, parsed.feed.updated)

    @patch.object(updater, "POST_LIMIT", 2)
    def test_collects_later_posts_on_scroll(self):
        lazy = """<script>window.addEventListener('wheel', () => {
            const post = document.querySelector('shreddit-post').cloneNode(true);
            post.setAttribute('permalink', '/r/test/comments/def/world/');
            document.body.append(post);
        }, {once: true});</script>"""
        data = self.scrape(USER, POST + lazy)
        self.assertEqual([entry.link.split("/")[-3] for entry in data.entries], ["abc", "def"])

    def test_signed_out_or_empty_listing_fails(self):
        for user, posts in [("", POST), (USER, "")]:
            with self.subTest(user=user), self.assertRaises(ValueError):
                self.scrape(user, posts)

    def test_malformed_session_fails_before_public_fetch(self):
        with patch.dict(os.environ, REDDIT_STORAGE_STATE="invalid"), patch.object(updater, "fetch_feed") as fetch:
            self.assertEqual(updater.main(), 1)
            fetch.assert_not_called()
