# Reddit RSS/ATOM Proxy

![Logo](reddit_rss_2_small.png)

This helps Feedly or FreshRSS grab the latest RSS feeds for various subreddits from Reddit by acting as an Atom proxy.

To use it:

- Fork the repo
- List the subreddit names you want in subreddits.csv
- Give GitHub Actions Read/Write permissions on your repo in: https://github.com/yourusername/reddit-rss-proxy/settings/actions
- GitHub Actions schedules a refresh at minute 17 of every hour (UTC). Scheduled jobs wait a random 0–120 seconds before starting work. Manual runs skip this delay.
- The feed files are then in /feeds
- Give Feedly a feed URL for each one like:

[https://conoro.github.io/reddit-rss-proxy/feeds/openclaw.xml](https://conoro.github.io/reddit-rss-proxy/feeds/openclaw.xml)

### Use a personal Reddit login

For a local refresh, install the Python requirements and sign in once using a
dedicated Brave profile. Run these commands from the repository root:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
mkdir -p playwright/.auth/brave-test
brave --user-data-dir="$PWD/playwright/.auth/brave-test" https://www.reddit.com/login/
```

Close that Brave instance after signing in. With a graphical desktop available,
run the updater using the same profile:

```sh
REDDIT_BRAVE_PROFILE=playwright/.auth/brave-test python scripts/update_feeds.py
```

The scraper opens each modern Reddit `/r/<subreddit>/new/` listing in a
visible browser and scrolls for up to 25 unique posts. It preserves discussion
permalinks as Atom entry IDs and writes the batch under `feeds/`.

### GitHub Actions session

After signing in with the dedicated Brave profile above, close its window and
use the authenticated GitHub CLI to set the repository secret. Substitute your
own repository name when using a fork.

```sh
python scripts/save_reddit_login.py conoro/reddit-rss-proxy
```

The helper sends the Reddit cookies directly to the repository's encrypted
`REDDIT_STORAGE_STATE` secret. Repeat this step after renewing an expired
session. The hosted workflow runs Brave on a virtual display and waits 65
seconds between communities. A failed scrape preserves the previous batch.
List only communities you intend to publish.

Use **Actions → Update Subreddit Feeds → Run workflow** for an immediate refresh
after deploying. The local updater writes XML and the workflow commits it.

Run regression tests with `python -m unittest discover -s tests -v` after
installing the requirements.

LICENSE Apache-2.0

Copyright Conor O'Neill 2025, conor@conoroneill.com
