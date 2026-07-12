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

import os
import csv
import requests
import feedparser
from feedgen.feed import FeedGenerator
import subprocess
from urllib.parse import urljoin, urlparse


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

def main():
    """
    Reads 'subreddits.csv', retrieves each subreddit's Atom feed using requests,
    parses it with feedparser, generates a new Atom feed with feedgen,
    and writes to '<subreddit>.xml'.

    Finally, if any .xml files changed, commits and pushes those changes to the repo.
    """
    
    # Configure Git
    subprocess.run(["git", "config", "user.name", "github-actions"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions@github.com"], check=True)

    # Ensure subreddits.csv is present
    subreddits_file = "subreddits.csv"
    if not os.path.exists(subreddits_file):
        print(f"Could not find {subreddits_file}. Please ensure it exists in the repo.")
        return

    # Read the list of subreddits
    with open(subreddits_file, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        subreddits = [row[0].strip() for row in reader if row]

    for subreddit in subreddits:
        if not subreddit:
            continue

        # Even though it's ".rss", Reddit returns Atom XML
        feed_url = f"https://old.reddit.com/r/{subreddit}/.rss"
        print(f"Processing {feed_url}")

        # 1) Fetch the Atom feed with requests + custom User-Agent
        headers = {"User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:15.0) Gecko/20100101 Firefox/15.0.1"}
        try:
            response = requests.get(feed_url, headers=headers, timeout=30)
            response.raise_for_status()  # raise an error for 4xx/5xx
        except requests.exceptions.RequestException as e:
            print(f"Error fetching {feed_url}: {e}")
            continue

        # The feed data as string
        feed_text = response.text

        # 2) Parse the Atom feed text with feedparser
        feed_data = feedparser.parse(feed_text)

        # 3) Build a new Atom feed using feedgen
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
            fe = fg.add_entry()

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

        # 4) Generate and write to <subreddit>.xml
        atom_feed_str = fg.atom_str(pretty=True)
        xml_filename = f"feeds/{subreddit}.xml"
        with open(xml_filename, "wb") as xml_file:
            xml_file.write(atom_feed_str)

if __name__ == "__main__":
    main()
