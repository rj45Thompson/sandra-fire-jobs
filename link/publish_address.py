#!/usr/bin/env python3
"""
Tell the public page where this computer is.

A quick tunnel hands out a different address every time it starts, which is
why the address kept having to be carried by hand. It does not any more: this
writes the address into relay.json next to the page on GitHub Pages, and the
page re-reads that file on a timer. Start the bridge and an already-open page
connects on its own within about half a minute.

    python publish_address.py https://something.trycloudflare.com

Needs a GitHub token with contents:write on the one repository, in the
environment as GITHUB_TOKEN or on a GITHUB_TOKEN= line in .env beside this
file or one directory up. The token never leaves this machine - it is used
here, to talk to GitHub, and nowhere else.

Run it with no argument to clear the address, which puts the page back to
saying the assistant is not up.
"""

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

OWNER = os.environ.get("PAGES_OWNER", "rj45Thompson")
REPO = os.environ.get("PAGES_REPO", "starfighter")
BRANCH = os.environ.get("PAGES_BRANCH", "main")
PATH = os.environ.get("PAGES_RELAY_PATH", "muster/relay.json")
API = os.environ.get("GITHUB_API", "https://api.github.com")


def token() -> str:
    """Environment first, then a .env beside this file or one level up."""
    t = os.environ.get("GITHUB_TOKEN", "").strip()
    if t:
        return t
    here = Path(__file__).resolve().parent
    for env in (here / ".env", here.parent / ".env"):
        if not env.exists():
            continue
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*GITHUB_TOKEN\s*=\s*(.+?)\s*$", line)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    return ""


def call(method: str, url: str, tok: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + tok)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "muster-link")
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


def publish(url: str) -> int:
    tok = token()
    if not tok:
        print("No GITHUB_TOKEN. Put one on a GITHUB_TOKEN= line in .env.")
        print("Make it at github.com/settings/tokens - fine-grained, this one")
        print(f"repository ({OWNER}/{REPO}), Contents: read and write.")
        return 2

    if url and not re.match(r"^https://[a-z0-9.-]+(/|$)", url, re.I):
        print(f"That does not look like an https address: {url}")
        return 2

    want = json.dumps({"url": url}, indent=2) + "\n"
    base = f"{API}/repos/{OWNER}/{REPO}/contents/{PATH}"

    sha = None
    try:
        cur = call("GET", base + "?ref=" + BRANCH, tok)
        sha = cur.get("sha")
        # Already right: say so and leave the history alone.
        now = base64.b64decode(cur.get("content", "")).decode("utf-8", "replace")
        if json.loads(now or "{}").get("url", "") == url:
            print(f"Address already published: {url or '(none)'}")
            return 0
    except urllib.error.HTTPError as e:
        if e.code != 404:              # 404 just means it is not there yet
            print(f"Could not read {PATH}: {e.code} {e.reason}")
            return 1
    except Exception as e:             # noqa: BLE001 - report, do not crash
        print(f"Could not read {PATH}: {e}")
        return 1

    body = {
        "message": "Muster: point the page at the desk",
        "content": base64.b64encode(want.encode()).decode(),
        "branch": BRANCH,
    }
    if sha:
        body["sha"] = sha

    try:
        call("PUT", base, tok, body)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        print(f"GitHub refused the write: {e.code} {e.reason} {detail}")
        if e.code in (401, 403):
            print("The token is missing Contents: read and write on this repo.")
        return 1
    except Exception as e:             # noqa: BLE001
        print(f"Could not write {PATH}: {e}")
        return 1

    print(f"Published: {url or '(cleared)'}")
    print("The page picks it up within about half a minute of Pages rebuilding.")
    return 0


if __name__ == "__main__":
    sys.exit(publish(sys.argv[1].strip() if len(sys.argv) > 1 else ""))
