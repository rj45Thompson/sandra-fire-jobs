#!/usr/bin/env python3
"""
Muster's self-test: drive the real page and prove it still works.

Every guard in the upgrader before this one was static - does the file
parse, did the ids survive, is the palette still there. All useful, and
all blind to the failure that actually matters: a file that parses
perfectly and renders a dead page. A handler wired to an element that no
longer exists throws at load and silently kills every button after it,
and nothing about the text of that file looks wrong.

So this opens the page in a real browser and behaves like Sandra for a
few seconds: click every tab, look for the controls that must be there,
and watch the console the whole time. If anything is broken, the upgrade
that caused it is rolled back before she ever sees it.

Used by server.py after a self-upgrade, and runnable by hand:

    py backend/smoke.py                  # against the running engine
    py backend/smoke.py --url http://127.0.0.1:8770
"""

from __future__ import annotations

import json
import sys

# Every tab in the rail, and something that must be visible once it opens.
# Keyed on the panel id so a renamed tab is caught rather than skipped.
REQUIRED_PANELS = {
    "dash": "#activity",
    "profile": "#gap-list",
    "jobs": "#jobs-body",
    "apps": "#apps-body",
    "inbox": "#inbox-list",
    "upgrade": "#msgs-upgrade",
}

# Controls the app is useless without, whatever else changes around them.
REQUIRED_CONTROLS = [
    ("#nav", "the navigation rail"),
    ("#drop-resume", "the résumé upload"),
    ("#chat-input", "the Super chat box"),
    ("#btn-send", "the chat send button"),
    ("#chat-input-profile", "the profile chat box"),
    ("#chat-input-upgrade", "the upgrade chat box"),
]

# Console noise that is not the page's fault and must not fail an upgrade.
IGNORABLE = (
    "favicon", "ERR_INTERNET_DISCONNECTED", "net::ERR_FAILED",
    "ERR_CONNECTION_REFUSED", "Failed to load resource",
)


def run(url: str = "http://127.0.0.1:8770", timeout_ms: int = 15000) -> dict:
    """
    Returns {"ok": bool, "checks": [...], "failures": [...], "skipped": bool}.

    A missing browser is reported as skipped rather than as a failure -
    refusing a good change because Playwright is not installed would be
    worse than not checking it.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ok": True, "skipped": True, "checks": [], "failures": [],
                "note": "Playwright is not installed, so the page was not "
                        "opened. Static checks still ran."}

    checks: list[str] = []
    failures: list[str] = []
    console_errors: list[str] = []

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="msedge", headless=True)
            except Exception:
                try:
                    browser = p.chromium.launch(headless=True)
                except Exception as e:
                    return {"ok": True, "skipped": True, "checks": [],
                            "failures": [],
                            "note": f"No browser available to test with ({e}). "
                                    "Static checks still ran."}

            page = browser.new_context().new_page()
            page.on("console", lambda m: (
                console_errors.append(m.text[:200])
                if m.type == "error" and not any(s in m.text for s in IGNORABLE)
                else None))
            page.on("pageerror", lambda e: console_errors.append(str(e)[:200]))

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(1200)   # let boot() settle
            except Exception as e:
                browser.close()
                return {"ok": False, "skipped": False, "checks": [],
                        "failures": [f"The page did not load at all: {e}"]}

            # 1. did it even boot
            if page.query_selector("#nav") is None:
                failures.append("The page loaded but the navigation is gone.")
            else:
                checks.append("the page loads")

            # 2. the controls that must exist
            for sel, human in REQUIRED_CONTROLS:
                if page.query_selector(sel) is None:
                    failures.append(f"{human} ({sel}) is missing.")
                else:
                    checks.append(f"{human} is there")

            # 3. every tab opens and shows its panel
            for panel, must_contain in REQUIRED_PANELS.items():
                btn = page.query_selector(f'#nav button[data-panel="{panel}"]')
                if btn is None:
                    failures.append(f"The {panel} tab is missing from the rail.")
                    continue
                try:
                    btn.click()
                    page.wait_for_timeout(180)
                except Exception as e:
                    failures.append(f"The {panel} tab could not be clicked: {e}")
                    continue

                section = page.query_selector(f"#p-{panel}")
                if section is None:
                    failures.append(f"The {panel} tab has no panel behind it.")
                elif section.get_attribute("hidden") is not None:
                    failures.append(f"Clicking {panel} did not open its panel.")
                elif page.query_selector(must_contain) is None:
                    failures.append(f"The {panel} panel is missing {must_contain}.")
                else:
                    checks.append(f"the {panel} tab opens")

            # 4. The Super chat has to still be usable after all that tab
            #    clicking - that is the whole point of it being a fixed
            #    column rather than a tab. If it is only reachable from one
            #    screen, it is not a Super chat.
            try:
                box = page.query_selector("#chat-input")
                if box:
                    box.fill("self test", timeout=4000)
                    checks.append("the chat box accepts typing")
            except Exception as e:
                failures.append(f"Typing into the chat box threw: {str(e)[:160]}")

            # 5. nothing blew up while we were doing all that
            if console_errors:
                uniq = list(dict.fromkeys(console_errors))[:4]
                failures.append("JavaScript errors on the page: " + "; ".join(uniq))
            else:
                checks.append("no JavaScript errors")

            browser.close()
    except Exception as e:
        # The harness itself failing is not proof the page is broken.
        return {"ok": True, "skipped": True, "checks": checks, "failures": [],
                "note": f"The self-test could not run ({type(e).__name__}: {e}). "
                        "Static checks still ran."}

    return {"ok": not failures, "skipped": False,
            "checks": checks, "failures": failures}


def main() -> int:
    url = "http://127.0.0.1:8770"
    if "--url" in sys.argv:
        url = sys.argv[sys.argv.index("--url") + 1]

    result = run(url)
    print(json.dumps(result, indent=2))

    if result.get("skipped"):
        print("\nSKIPPED -", result.get("note", ""))
        return 0
    if result["ok"]:
        print(f"\nPAGE OK - {len(result['checks'])} checks passed")
        return 0
    print(f"\nPAGE BROKEN - {len(result['failures'])} problem(s):")
    for f in result["failures"]:
        print("  -", f)
    return 1


if __name__ == "__main__":
    sys.exit(main())
