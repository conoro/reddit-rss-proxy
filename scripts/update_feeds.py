# /// script
# requires-python = ">=3.12.2"
# dependencies = [
#   "feedparser",
#   "feedgen",
#   "requests",
#   "playwright",
# ]
# ///
# This script reads a list of subreddits from a CSV file, fetches their RSS feeds, and generates ATOM feeds for each subreddit.
#!/usr/bin/env python3

import csv
import json
import re
from contextlib import ExitStack
from datetime import datetime
from html import escape
import os
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
from feedgen.feed import FeedGenerator
import requests
from playwright.sync_api import Error as BrowserError, TimeoutError as BrowserTimeout, sync_playwright

REDDIT_BASE_URL = "https://old.reddit.com"


def absolute_url(value, base_url=REDDIT_BASE_URL):
    """Return value as an absolute URL, or an empty string if it cannot be one."""
    if not value:
        return ""

    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        return value
    if value.startswith("/"):
        return urljoin(base_url, value)

    return ""


def reddit_url(path):
    return urljoin(REDDIT_BASE_URL, path)


# Keep the historical base for relative Atom IDs so existing subscriptions do not
# see every post as new when the fetch endpoint changes.
FEED_BASE_URL = "https://www.reddit.com"
REQUEST_INTERVAL = 65
HEADERS = {
    "User-Agent": "reddit-rss-proxy/1.0 (https://github.com/conoro/reddit-rss-proxy)",
    "Accept": "application/atom+xml, application/rss+xml",
}


def fetch_feed(feed_url):
    """Fetch actual feed XML; HTML login pages can also return HTTP 200."""
    for attempt in range(2):
        response = requests.get(feed_url, headers=HEADERS, timeout=30)
        if response.status_code == 429 and attempt == 0:
            try:
                delay = float(response.headers.get("Retry-After", REQUEST_INTERVAL))
            except ValueError:
                delay = REQUEST_INTERVAL
            time.sleep(max(REQUEST_INTERVAL, min(delay, 120)))
            continue
        response.raise_for_status()
        feed_data = feedparser.parse(response.content)
        if feed_data.bozo or not feed_data.get("version") or not feed_data.entries:
            raise ValueError(f"Expected a non-empty valid feed from {response.url}")
        return feed_data


def render_feed(feed_data, feed_url, subreddit):
    """Convert a validated Reddit feed to the proxy's Atom format."""
    fg = FeedGenerator()

    # Top-level feed fields
    feed_title   = getattr(feed_data.feed, 'title',    f"r/{subreddit}")
    feed_link    = absolute_url(getattr(feed_data.feed, 'link', '')) or feed_url
    feed_desc    = getattr(feed_data.feed, 'subtitle', f"Atom feed for r/{subreddit}")
    feed_icon    = absolute_url(getattr(feed_data.feed, 'icon', ''))
    feed_id      = absolute_url(getattr(feed_data.feed, 'id', '')) or feed_url
    feed_updated = getattr(feed_data.feed, 'updated',  '')

    fg.title(feed_title)
    fg.link({'href': feed_link, 'rel': 'alternate'})
    fg.id(feed_id)
    fg.description(feed_desc)

    if feed_icon:
        fg.icon(feed_icon)
    if feed_updated:
        fg.updated(feed_updated)

    # Copy entries
    for entry_index, entry in enumerate(feed_data.entries, start=1):
        fe = fg.add_entry(order="append")

        # Author info (Reddit’s <author><name>...<uri>...</author>)
        author_name = getattr(entry, 'author', None)
        author_href = getattr(entry, 'author_detail', {}).get('href', None)
        if author_name or author_href:
            author_dict = {'name': author_name} if author_name else {}
            if author_href:
                author_dict['uri'] = author_href
            fe.author(author_dict)

        # Tags become categories
        if hasattr(entry, 'tags'):
            for tag in entry.tags:
                term = getattr(tag, 'term', None)
                label = getattr(tag, 'label', term)
                if term:
                    fe.category(term=term, label=label)

        entry_title     = getattr(entry, 'title', 'No Title')
        raw_entry_link  = getattr(entry, 'link', '')
        raw_entry_id    = getattr(entry, 'id', '')
        entry_link      = absolute_url(raw_entry_link)
        entry_id        = (
            absolute_url(raw_entry_id)
            or entry_link
            or (reddit_url(f"/{raw_entry_id}") if raw_entry_id else f"{feed_url}#entry-{entry_index}")
        )
        entry_updated   = getattr(entry, 'updated', '')
        entry_published = getattr(entry, 'published', '')
        entry_content   = getattr(entry, 'content', None)
        entry_summary   = getattr(entry, 'summary', '')

        fe.title(entry_title)
        fe.link({'href': entry_link})
        fe.id(entry_id)

        if entry_updated:
            fe.updated(entry_updated)
        if entry_published:
            fe.published(entry_published)

        # In Atom, 'content' might be a list of { 'type': 'html', 'value': '...' }
        if entry_content and isinstance(entry_content, list):
            content_value = entry_content[0].get('value', '')
            fe.content(content_value, type='html')
        else:
            # fallback to 'summary'
            fe.summary(entry_summary)

    return fg.atom_str(pretty=True)


POST_LIMIT = 25


def scrape_listing(page, subreddit):
    """Collect unique posts as the modern new-post listing loads on scroll."""
    url = f"{FEED_BASE_URL}/r/{subreddit}/new/"
    response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
    if not response or not response.ok:
        raise ValueError(f"Listing request failed (HTTP {response.status if response else 'unknown'})")
    if page.url.rstrip("/").lower() != url.rstrip("/").lower():
        raise ValueError("Reddit redirected the listing. Check the saved login.")
    if not page.locator('shreddit-app[user-logged-in="true"]').count():
        raise ValueError("Reddit session is signed out. Renew the saved login.")

    entries = []
    seen = set()
    listing = page.locator("shreddit-post[permalink]")
    for _ in range(POST_LIMIT):
        posts = listing.evaluate_all("""posts => posts.map(post => ({
            path: post.getAttribute('permalink'),
            title: post.getAttribute('post-title'),
            timestamp: post.getAttribute('created-timestamp'),
            author: post.getAttribute('author'),
            target: post.getAttribute('content-href'),
            text: post.querySelector('[slot="text-body"]')?.innerText || '',
            promoted: post.hasAttribute('promoted') || !!post.closest('shreddit-ad-post')
        }))""")
        if not posts:
            raise ValueError("No posts found in the signed-in subreddit listing")
        for post in posts:
            match = re.fullmatch(r"/r/([^/]+)/comments/([a-z0-9]+)(?:/[^/?#]*)?/?", post["path"])
            if post["promoted"] or not match or match[1].lower() != subreddit.lower() or match[2] in seen:
                continue
            seen.add(match[2])
            title = (post["title"] or "").strip()
            published = datetime.fromisoformat(post["timestamp"] or "")
            if not title or published.tzinfo is None:
                raise ValueError("Post has an invalid title or timestamp")
            link = urljoin(FEED_BASE_URL, post["path"])
            target = urljoin(FEED_BASE_URL, post["target"] or post["path"])
            content = escape(post["text"].strip()).replace("\n", "<br>")
            if urlparse(target).scheme in {"http", "https"}:
                content += f' <a href="{escape(target, quote=True)}">[link]</a>'
            entries.append(feedparser.FeedParserDict(
                title=title, id=link, link=link, author=f"/u/{post['author'] or '[deleted]'}",
                published=published.isoformat(), updated=published.isoformat(),
                content=[{"value": content.strip()}],
            ))
            if len(entries) == POST_LIMIT:
                break
        if len(entries) == POST_LIMIT:
            break
        listing.last.scroll_into_view_if_needed()
        page.mouse.wheel(0, 850)
        try:
            page.wait_for_function(
                """last => Array.from(document.querySelectorAll('shreddit-post[permalink]'))
                    .at(-1)?.getAttribute('permalink') !== last""",
                arg=posts[-1]["path"], timeout=10000,
            )
        except BrowserTimeout:
            break
    if not entries:
        raise ValueError("No matching posts in the signed-in subreddit listing")
    metadata = feedparser.FeedParserDict(
        id=reddit_url(f"/r/{subreddit}/.rss"), title=f"r/{subreddit}",
        link=f"{FEED_BASE_URL}/r/{subreddit}/",
        updated=max((entry.updated for entry in entries), key=datetime.fromisoformat),
    )
    return feedparser.FeedParserDict(feed=metadata, entries=entries)


def main():
    try:
        with ExitStack() as stack:
            state = os.environ.get("REDDIT_STORAGE_STATE")
            page = None
            profile = os.environ.get("REDDIT_BRAVE_PROFILE")
            if profile:
                directory = Path(profile).resolve()
                brave = shutil.which("brave") or shutil.which("brave-browser")
                if not brave or not (directory / "Default").is_dir():
                    raise ValueError("Expected an installed Brave browser and a signed-in dedicated profile.")
                playwright = stack.enter_context(sync_playwright())
                context = playwright.chromium.launch_persistent_context(
                    directory, executable_path=brave, headless=False,
                    ignore_default_args=["--password-store=basic", "--use-mock-keychain"],
                )
                stack.callback(context.close)
                page = context.new_page()
            elif state:
                try:
                    session = json.loads(state)
                    if not isinstance(session, dict) or not isinstance(session.get("cookies"), list):
                        raise ValueError()
                except ValueError:
                    raise ValueError("Invalid REDDIT_STORAGE_STATE. Renew the saved login.") from None
                playwright = stack.enter_context(sync_playwright())
                browser = playwright.chromium.launch(
                    executable_path=os.environ.get("REDDIT_BROWSER_EXECUTABLE"), headless=False,
                )
                stack.callback(browser.close)
                try:
                    context = browser.new_context(storage_state=session)
                except BrowserError:
                    raise ValueError("Could not load REDDIT_STORAGE_STATE. Renew the saved login.") from None
                page = context.new_page()
            return refresh_feeds(page)
    except (BrowserError, ValueError) as error:
        print(f"Feed refresh failed: {error}", file=sys.stderr)
        return 1


def refresh_feeds(page=None):
    """Refresh feeds only after every source has been fetched and validated."""
    with open("subreddits.csv", newline="", encoding="utf-8") as source:
        subreddits = list(dict.fromkeys(
            row[0].strip() for row in csv.reader(source) if row and row[0].strip()
        ))
    if not subreddits:
        print("No subreddits configured", file=sys.stderr)
        return 1

    if any(not re.fullmatch(r"[A-Za-z0-9_]+", name) for name in subreddits):
        raise ValueError("Expected subreddit names in subreddits.csv")

    pending = {}
    failures = []
    for index, subreddit in enumerate(subreddits):
        if index:
            time.sleep(REQUEST_INTERVAL)
        feed_url = f"{FEED_BASE_URL}/r/{subreddit}/.rss"
        print(f"Processing r/{subreddit}", flush=True)
        try:
            feed_data = scrape_listing(page, subreddit) if page is not None else fetch_feed(feed_url)
            pending[subreddit] = render_feed(feed_data, feed_url, subreddit)
            print(f"Validated {len(feed_data.entries)} entries for r/{subreddit}", flush=True)
        except (requests.exceptions.RequestException, BrowserError, ValueError) as error:
            print(f"Error updating r/{subreddit}: {error}", file=sys.stderr)
            failures.append(subreddit)

    if failures:
        print("Keeping existing feeds; failed: " + ", ".join(failures), file=sys.stderr)
        return 1

    Path("feeds").mkdir(exist_ok=True)
    for subreddit, content in pending.items():
        destination = Path("feeds") / f"{subreddit}.xml"
        temporary = destination.with_suffix(".xml.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, destination)
    return 0


if __name__ == "__main__":
    sys.exit(main())
