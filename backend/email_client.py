#!/usr/bin/env python3
"""
Muster email watcher - reads Gmail over IMAP with an App Password.

Google stopped accepting account passwords for mail in 2022, so this uses a
Gmail App Password (Google Account -> Security -> App passwords). The password
lives only in .env on this machine, and is used read-only to spot replies,
interview invites and rejections and thread them onto the right application.
"""

import email
import imaplib
import re
from datetime import datetime
from email.header import decode_header

IMAP_HOST = "imap.gmail.com"


def _decode(raw) -> str:
    if not raw:
        return ""
    parts = decode_header(raw)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", "ignore"))
        else:
            out.append(text)
    return "".join(out)


def test_login(address: str, app_password: str) -> tuple[bool, str]:
    """Verify the App Password works. Returns (ok, message)."""
    if not address or not app_password:
        return False, "Need both an email address and an app password."
    try:
        m = imaplib.IMAP4_SSL(IMAP_HOST)
        m.login(address, app_password.replace(" ", ""))
        m.select("INBOX")
        m.logout()
        return True, "Connected to Gmail."
    except imaplib.IMAP4.error as e:
        msg = str(e)
        if "Invalid credentials" in msg or "AUTHENTICATIONFAILED" in msg:
            return False, ("Gmail rejected that. Make sure it is a 16-character "
                           "App Password, not your normal password, and that "
                           "2-Step Verification is on.")
        return False, f"Gmail error: {msg[:120]}"
    except OSError as e:
        return False, f"Could not reach Gmail: {e}"


def _classify(subject: str, body: str) -> str:
    t = (subject + " " + body).lower()
    if re.search(r"interview|phone screen|meet with|schedule a (call|time)|"
                 r"like to speak|availability", t):
        return "interview"
    if re.search(r"unfortunately|not (moving|proceeding)|other candidate|"
                 r"decided not to|position has been filled|will not be", t):
        return "rejection"
    if re.search(r"received your application|thank you for applying|"
                 r"application (has been )?received|confirm", t):
        return "acknowledgement"
    return "reply"


def scan_inbox(address: str, app_password: str, days: int = 30,
               limit: int = 40) -> dict:
    """Return recent messages that look job-related, newest first."""
    ok, msg = (bool(address and app_password), "")
    if not ok:
        return {"ok": False, "error": "Email is not connected yet.", "messages": []}
    try:
        m = imaplib.IMAP4_SSL(IMAP_HOST)
        m.login(address, app_password.replace(" ", ""))
        m.select("INBOX")
        since = (datetime.now().strftime("%d-%b-%Y"))
        # search by date window; keyword filter happens in Python
        typ, data = m.search(None, f'(SINCE {_since(days)})')
        ids = data[0].split()[-limit:]
        out = []
        JOBWORDS = re.compile(
            r"applic|position|role|firefighter|nurse|care aide|recruit|hiring|"
            r"career|interview|candidate|posting|fire department|paramedic", re.I)
        for i in reversed(ids):
            typ, d = m.fetch(i, "(RFC822)")
            if typ != "OK" or not d or not d[0]:
                continue
            msgobj = email.message_from_bytes(d[0][1])
            subject = _decode(msgobj.get("Subject"))
            sender = _decode(msgobj.get("From"))
            body = _body(msgobj)
            if not JOBWORDS.search(subject + " " + sender + " " + body[:400]):
                continue
            out.append({
                "from": sender, "subject": subject,
                "date": _decode(msgobj.get("Date")),
                "kind": _classify(subject, body),
                "snippet": re.sub(r"\s+", " ", body)[:200],
            })
        m.logout()
        return {"ok": True, "messages": out}
    except (imaplib.IMAP4.error, OSError) as e:
        return {"ok": False, "error": str(e)[:160], "messages": []}


def _since(days: int) -> str:
    from datetime import timedelta
    return (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")


def _body(msgobj) -> str:
    if msgobj.is_multipart():
        for part in msgobj.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", "ignore")
                except (AttributeError, UnicodeDecodeError):
                    continue
        return ""
    try:
        return msgobj.get_payload(decode=True).decode(
            msgobj.get_content_charset() or "utf-8", "ignore")
    except (AttributeError, UnicodeDecodeError):
        return ""
