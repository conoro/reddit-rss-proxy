# /// script
# requires-python = ">=3.12.2"
# dependencies = [
#   "feedparser",
#   "feedgen",
#   "requests",
# ]
# ///
# This script reads a list of subreddits from a CSV file, fetches their RSS feeds, and generates ATOM feeds for each subreddit.
#!/usr/bin/env python3

import csv
import os
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
from feedgen.feed import FeedGenerator
import requests

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


def main():
    """Refresh feeds only after every source has been fetched and validated."""
    with open("subreddits.csv", newline="", encoding="utf-8") as source:
        subreddits = list(dict.fromkeys(
            row[0].strip() for row in csv.reader(source) if row and row[0].strip()
        ))
    if not subreddits:
        print("No subreddits configured", file=sys.stderr)
        return 1

    pending = {}
    failures = []
    for index, subreddit in enumerate(subreddits):
        if index:
            time.sleep(REQUEST_INTERVAL)
        feed_url = f"{FEED_BASE_URL}/r/{subreddit}/.rss"
        print(f"Processing {feed_url}", flush=True)
        try:
            feed_data = fetch_feed(feed_url)
            pending[subreddit] = render_feed(feed_data, feed_url, subreddit)
            print(f"Validated {len(feed_data.entries)} entries for r/{subreddit}", flush=True)
        except (requests.exceptions.RequestException, ValueError) as error:
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
