#!/usr/bin/env python3
"""
Muster applier - fills a job application the same way we do by hand.

Opens the posting in a real browser, matches each form field to the profile
by its label/name/placeholder, types the answers, attaches the résumé, and
then STOPS. It never clicks submit while AUTO_SUBMIT is false: Sandra reviews
the filled form and sends it herself, exactly like the review queue we built
for RJ.

Driven from server.py via /apply/start. Uses the system browser through a
Playwright channel, so nothing had to be downloaded.
"""

import re
from pathlib import Path

# name/label patterns -> profile key. First match wins, so order matters:
# put the more specific patterns first.
FIELD_MAP = [
    (r"first[\s_-]*name|given[\s_-]*name|fname", "first_name"),
    (r"last[\s_-]*name|sur[\s_-]*name|family[\s_-]*name|lname", "last_name"),
    (r"full[\s_-]*name|your[\s_-]*name|^name$|applicant[\s_-]*name", "full_name"),
    (r"e-?mail", "email"),
    (r"phone|mobile|cell|telephone|contact[\s_-]*number", "phone"),
    (r"street|address[\s_-]*1|addr(ess)?$|mailing", "address"),
    (r"city|town|municipality", "city"),
    (r"province|state|region", "province"),
    (r"postal|zip", "postal"),
    (r"country", "country"),
    (r"linked[\s_-]*in", "linkedin"),
    (r"git[\s_-]*hub|portfolio|website|personal[\s_-]*site", "website"),
    (r"current[\s_-]*(employer|company)|present[\s_-]*employer", "current_dept"),
    (r"current[\s_-]*(role|title|position)|occupation|job[\s_-]*title", "rank"),
]

RESUME_ACCEPT = (".pdf", ".doc", ".docx", ".rtf", ".odt", ".txt")


def _val(profile: dict, key: str) -> str:
    if key == "full_name":
        return f"{profile.get('first_name','')} {profile.get('last_name','')}".strip()
    if key == "country":
        return profile.get("country") or "Canada"
    return str(profile.get(key, "") or "")


def _match(label: str) -> str | None:
    low = label.lower()
    for pat, key in FIELD_MAP:
        if re.search(pat, low):
            return key
    return None


def fill_application(url: str, profile: dict, resume_path: str | None,
                     channel: str = "msedge", headless: bool = False) -> dict:
    """
    Open url, fill what we can, attach the résumé, and stop before submit.
    Returns a report of what was filled and what still needs a human.
    """
    from playwright.sync_api import sync_playwright

    filled, skipped, notes = [], [], []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel=channel, headless=headless)
        except Exception:
            browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
        except Exception as e:
            browser.close()
            return {"ok": False, "error": f"Could not open the page: {e}",
                    "filled": [], "skipped": []}

        # bot-detection or a login wall means a human is needed
        html = (page.content() or "").lower()
        if any(w in html for w in ("recaptcha", "hcaptcha", "cloudflare",
                                   "are you human", "sign in to continue")):
            notes.append("This page has a sign-in or bot check, so it needs you "
                         "to finish it in person. The browser is open for you.")

        # text-like inputs and textareas
        for el in page.query_selector_all(
                "input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=file]), textarea"):
            try:
                itype = (el.get_attribute("type") or "text").lower()
                if itype in ("checkbox", "radio", "password"):
                    continue
                label = " ".join(filter(None, [
                    el.get_attribute("name"), el.get_attribute("id"),
                    el.get_attribute("aria-label"), el.get_attribute("placeholder")]))
                # a linked <label for=...>
                fid = el.get_attribute("id")
                if fid:
                    lab = page.query_selector(f"label[for='{fid}']")
                    if lab:
                        label += " " + (lab.inner_text() or "")
                key = _match(label)
                if not key:
                    continue
                val = _val(profile, key)
                if not val:
                    skipped.append(f"{key} (no value in profile)")
                    continue
                if el.input_value():        # already has something, leave it
                    continue
                el.fill(val)
                filled.append(f"{key} = {val[:40]}")
            except Exception:
                continue

        # résumé into any document file input
        if resume_path and Path(resume_path).exists():
            for fi in page.query_selector_all("input[type=file]"):
                try:
                    accept = (fi.get_attribute("accept") or "").lower()
                    if accept and not any(a in accept for a in RESUME_ACCEPT):
                        continue
                    fi.set_input_files(resume_path)
                    filled.append("résumé attached")
                    break
                except Exception:
                    continue
        elif not resume_path:
            notes.append("No résumé on file yet - upload one in Documents and it "
                         "will attach automatically next time.")

        # leave the browser open (headed) so she can review and submit
        if headless:
            browser.close()

    return {"ok": True, "url": url, "filled": filled, "skipped": skipped,
            "notes": notes, "left_open": not headless}


if __name__ == "__main__":   # quick manual check against a local form
    import sys
    demo = {"first_name": "Sandra", "last_name": "Ayany",
            "email": "sandra@example.com", "phone": "780-555-1212",
            "city": "Onoway", "province": "Alberta"}
    print(fill_application(sys.argv[1], demo, None, headless=True))
