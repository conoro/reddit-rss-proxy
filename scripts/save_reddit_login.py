"""Upload Reddit cookies from the dedicated Brave profile to GitHub Actions."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

from playwright.sync_api import sync_playwright


def main():
    profile = Path("playwright/.auth/brave-test").resolve()
    brave = shutil.which("brave") or shutil.which("brave-browser")
    if not brave or not (profile / "Default").is_dir():
        raise SystemExit("Sign in with the dedicated Brave profile first.")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            profile, executable_path=brave, headless=False,
            ignore_default_args=["--password-store=basic", "--use-mock-keychain"],
        )
        try:
            cookies = [cookie for cookie in context.cookies(["https://www.reddit.com/"])
                       if cookie["domain"].lstrip(".") in {"reddit.com", "www.reddit.com"}]
        finally:
            context.close()
    if not any(cookie["name"] == "reddit_session" for cookie in cookies):
        raise SystemExit("Missing Reddit session. Sign in to the dedicated profile again.")
    state = json.dumps({"cookies": cookies, "origins": []})
    subprocess.run(["gh", "secret", "set", "REDDIT_STORAGE_STATE", "--repo", sys.argv[1]],
                   input=state, text=True, check=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/save_reddit_login.py OWNER/REPO")
    main()
