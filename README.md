# Reddit RSS/ATOM Proxy

![Logo](reddit_rss_2_small.png)

This helps Feedly or FreshRSS grab the latest RSS feeds for various subreddits from Reddit by acting as an Atom proxy.

To use it:

- Fork the repo
- List the subreddit names you want in subreddits.csv
- Give GitHub Actions Read/Write permissions on your repo in: https://github.com/yourusername/reddit-rss-proxy/settings/actions
- GitHub Actions should run every 15 minutes
- The feed files are then in /feeds
- Give Feedly a feed URL for each one like:

[https://conoro.github.io/reddit-rss-proxy/feeds/openclaw.xml](https://conoro.github.io/reddit-rss-proxy/feeds/openclaw.xml)

The updater fetches `https://www.reddit.com/r/<subreddit>/.rss`.
Old Reddit now redirects logged-out feed requests to a login page. Requests
are spaced 65 seconds apart to respect Reddit's rate limit, with one bounded
retry for HTTP 429. A run takes several minutes when multiple feeds are configured.

If any request fails or returns HTML, malformed XML, or no entries, the updater
keeps all existing feed files and fails the Actions run instead of publishing
empty feeds. Check the **Update Subreddit Feeds** Actions logs if feeds go stale.

To refresh locally: `uv run scripts/update_feeds.py` (or install
`requirements.txt` and run `python scripts/update_feeds.py`). The script only
writes feed files; committing and pushing are handled by the workflow.

Run regression tests with `python -m unittest discover -s tests -v` after
installing the requirements.

LICENSE Apache-2.0

Copyright Conor O'Neill 2025, conor@conoroneill.com
