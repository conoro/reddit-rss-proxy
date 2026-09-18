import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import feedparser
import requests
from scripts import update_feeds as updater

ATOM = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<title>Test</title><id>/r/test/.rss</id><updated>2026-09-18T00:00:00Z</updated>
<entry><id>t3_first</id><title>First</title><link href="/r/test/comments/first/"/>
<updated>2026-09-18T00:00:00Z</updated><content type="html">Hello</content></entry>
<entry><id>t3_second</id><title>Second</title><link href="/r/test/comments/second/"/>
<updated>2026-09-17T00:00:00Z</updated></entry></feed>'''


def response(content=ATOM, status=200, headers=None):
    result = Mock(content=content, status_code=status, headers=headers or {},
                  url='https://www.reddit.com/r/test/.rss')
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(str(status))
    return result


class FeedTests(unittest.TestCase):
    @patch.object(updater.requests, 'get')
    def test_rejects_html_empty_and_malformed_feeds(self, get):
        for content in (b'<html><body>Log in</body></html>', b'',
                        b'<feed xmlns="http://www.w3.org/2005/Atom"/>', ATOM[:-7]):
            with self.subTest(content=content):
                get.return_value = response(content)
                with self.assertRaises(ValueError):
                    updater.fetch_feed('https://www.reddit.com/r/test/.rss')

    @patch.object(updater.time, 'sleep')
    @patch.object(updater.requests, 'get')
    def test_rate_limit_retries_once(self, get, sleep):
        get.side_effect = [response(status=429, headers={'Retry-After': '90'}), response()]
        self.assertEqual(len(updater.fetch_feed('https://www.reddit.com/r/test/.rss').entries), 2)
        sleep.assert_called_once_with(90)
        get.side_effect = [response(status=429), response(status=429)]
        with self.assertRaises(requests.HTTPError):
            updater.fetch_feed('https://www.reddit.com/r/test/.rss')

    def test_render_preserves_entry_identity_content_and_order(self):
        output = feedparser.parse(updater.render_feed(feedparser.parse(ATOM),
                                'https://www.reddit.com/r/test/.rss', 'test'))
        self.assertFalse(output.bozo)
        self.assertEqual([entry.title for entry in output.entries], ['First', 'Second'])
        self.assertEqual(output.feed.id, 'https://old.reddit.com/r/test/.rss')
        self.assertEqual(output.entries[0].id, 'https://old.reddit.com/r/test/comments/first/')
        self.assertEqual(output.entries[0].content[0].value, 'Hello')

    @patch.object(updater.time, 'sleep')
    @patch.object(updater.requests, 'get')
    def test_failed_batch_preserves_files_then_success_refreshes(self, get, sleep):
        previous = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                Path('subreddits.csv').write_text('test\nother\n')
                Path('feeds').mkdir()
                feed = Path('feeds/test.xml')
                feed.write_bytes(b'previous feed')
                get.side_effect = [response(), response(b'<html>Login</html>')]
                self.assertEqual(updater.main(), 1)
                self.assertEqual(feed.read_bytes(), b'previous feed')
                self.assertFalse(Path('feeds/other.xml').exists())
                get.side_effect = [response(), response()]
                self.assertEqual(updater.main(), 0)
                self.assertEqual(len(feedparser.parse(feed.read_bytes()).entries), 2)
                self.assertTrue(Path('feeds/other.xml').exists())
                self.assertEqual(get.call_args.args[0], 'https://www.reddit.com/r/other/.rss')
                sleep.assert_called_with(65)
            finally:
                os.chdir(previous)


if __name__ == '__main__':
    unittest.main()
