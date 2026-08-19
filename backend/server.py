#!/usr/bin/env python3
"""
Muster - local engine.

Runs on Sandra's machine. Holds the profile, documents and credentials;
the public GitHub Pages front-end talks to it over HTTP on localhost.

Nothing here is ever committed: data/ and .env are gitignored.

    py backend/server.py

Stdlib only. No pip install required to boot.
"""

import base64
import hmac
import http.cookies
import ipaddress
import json
import os
import secrets
import re
import shutil
import sqlite3
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone, date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "0.1.0"

# Windows consoles default to cp1252; force UTF-8 so the banner survives.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DOCS_DIR = DATA / "documents"
RESUME_DIR = DATA / "resumes"
DB_PATH = DATA / "muster.db"
WEB_DIR = ROOT / "docs"

for d in (DATA, DOCS_DIR, RESUME_DIR):
    d.mkdir(parents=True, exist_ok=True)


# ─────────────────────────── config ────────────────────────────
def load_env() -> dict:
    """Read .env if present. Never logged, never served."""
    env = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k, v in os.environ.items():
        env.setdefault(k, v)
    return env


ENV = load_env()
PORT = int(ENV.get("API_PORT", 8770))
TOKEN = ENV.get("API_TOKEN", "")

# Which interface to listen on. The default keeps the engine invisible to
# everything but this machine. Set BIND_HOST=0.0.0.0 to let other devices on
# the same home network reach it - a phone, a laptop in another room.
BIND_HOST = ENV.get("BIND_HOST", "127.0.0.1").strip() or "127.0.0.1"

# The address ranges a home network actually uses. Spelled out rather than
# using ipaddress.is_private, which also returns True for the documentation
# ranges (203.0.113.0/24 and friends) - those are not anyone's home LAN, and
# treating them as trusted would widen this rule for no reason. Carrier-grade
# NAT (100.64.0.0/10) is deliberately absent too: it is neither private nor
# global, and it is an ISP's shared space, not this house.
HOME_NETWORKS = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",   # RFC1918
    "169.254.0.0/16",                                   # link-local
    "fc00::/7", "fe80::/10",                            # IPv6 ULA + link-local
)]

# The PIN a NEW device has to enter once before it is allowed in. Only ever
# asked of devices arriving over the network; this machine is already trusted.
ACCESS_PIN = ENV.get("ACCESS_PIN", "").strip()
ALLOW_ORIGINS = [
    "https://rj45thompson.github.io",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "null",
]


# ─────────────────────────── storage ───────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
    key TEXT PRIMARY KEY, value TEXT);

-- Devices allowed in over the home network. A device proves itself once with
-- the PIN and is remembered by a random token in a cookie, so Sandra's phone
-- asks once and not every time.
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT UNIQUE NOT NULL,
    name TEXT,
    ip TEXT,
    first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
    last_seen TEXT DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS certs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, status TEXT, expiry TEXT,
    source TEXT DEFAULT 'manual',   -- manual | resume
    added_at TEXT DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS employers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    kind TEXT,              -- municipal | industrial | wildland | airport
    city TEXT, province TEXT,
    careers_url TEXT, ats TEXT,
    hires TEXT,             -- full-time | paid-on-call | both | seasonal
    notes TEXT);

CREATE TABLE IF NOT EXISTS postings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employer_id INTEGER REFERENCES employers(id),
    title TEXT, url TEXT UNIQUE,
    employment_type TEXT, city TEXT,
    posted TEXT, closes TEXT,
    match INTEGER DEFAULT 0,
    first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
    active INTEGER DEFAULT 1);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    posting_id INTEGER REFERENCES postings(id),
    status TEXT DEFAULT 'review',   -- review | submitted | replied | closed
    submitted_at TEXT, last_reply TEXT, notes TEXT);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT, filename TEXT, path TEXT,
    added_at TEXT DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT, at TEXT DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT, url TEXT UNIQUE, kind TEXT,
    added_at TEXT DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS chat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT, text TEXT, at TEXT DEFAULT CURRENT_TIMESTAMP);
"""

_PIN_TRIES: dict[str, list[float]] = {}
_local = threading.local()


def db() -> sqlite3.Connection:
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        # ThreadingHTTPServer gives every request its own thread, and every
        # thread its own connection here. Default SQLite journal mode takes
        # an exclusive lock for the duration of any write, which stalls every
        # other connection - including a plain health-check GET on a totally
        # unrelated thread - until it releases. WAL lets readers and the one
        # writer proceed concurrently; busy_timeout means a genuine collision
        # waits and retries instead of raising immediately.
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA busy_timeout=8000")
        _local.conn.executescript(SCHEMA)
        # migrations: add columns that older databases lack
        for tbl, col, decl in [("certs", "source", "TEXT DEFAULT 'manual'")]:
            cols = [r[1] for r in _local.conn.execute(f"PRAGMA table_info({tbl})")]
            if col not in cols:
                _local.conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {decl}")
        _local.conn.commit()
    return _local.conn


def log_event(text: str) -> None:
    db().execute("INSERT INTO events (text) VALUES (?)", (text,))
    db().commit()


# ──────────────────── employer seed data ───────────────────────
# Verified careers URLs. The research pass expands this list;
# these are the anchors that matter for the Edmonton region.
SEED_EMPLOYERS = [
    # ── municipal, Edmonton region ──
    ("City of Edmonton", "municipal", "Edmonton", "AB",
     "https://www.edmonton.ca/city_government/jobs", "custom", "full-time",
     "Large service. Firefighter intakes run in windows; NFPA 1001 I+II and EMR typically required."),
    ("Strathcona County", "municipal", "Sherwood Park", "AB",
     "https://www.strathcona.ca/careers/", "custom", "both",
     "Composite department, hires both career and paid-on-call."),
    ("Parkland County", "municipal", "Parkland County", "AB",
     "https://www.parklandcounty.com/en/live-and-play/careers.aspx", "custom", "paid-on-call",
     "Neighbouring county to Lac Ste. Anne. Deputy Chief Aaron Davies is a known contact."),
    ("City of Spruce Grove", "municipal", "Spruce Grove", "AB",
     "https://www.sprucegrove.org/government/careers/", "custom", "both",
     "Trevor Sutherley responded on training; department known to us."),
    ("City of St. Albert", "municipal", "St. Albert", "AB",
     "https://stalbert.ca/city/careers/", "custom", "full-time", ""),
    ("City of Leduc", "municipal", "Leduc", "AB",
     "https://www.leduc.ca/careers", "custom", "both", ""),
    ("Leduc County", "municipal", "Nisku", "AB",
     "https://www.leduc-county.com/en/county-office/careers.aspx", "custom", "paid-on-call", ""),
    ("City of Fort Saskatchewan", "municipal", "Fort Saskatchewan", "AB",
     "https://www.fortsask.ca/en/city-hall/careers.aspx", "custom", "both", ""),
    ("Town of Stony Plain", "municipal", "Stony Plain", "AB",
     "https://www.stonyplain.com/en/town-hall/careers.aspx", "custom", "paid-on-call", ""),
    ("Town of Morinville", "municipal", "Morinville", "AB",
     "https://www.morinville.ca/careers", "custom", "paid-on-call", ""),
    ("City of Beaumont", "municipal", "Beaumont", "AB",
     "https://www.beaumont.ab.ca/careers", "custom", "paid-on-call", ""),
    ("Sturgeon County", "municipal", "Morinville", "AB",
     "https://www.sturgeoncounty.ca/careers", "custom", "paid-on-call", ""),
    ("Lac Ste. Anne County", "municipal", "Sangudo", "AB",
     "https://www.lsac.ca/p/careers", "custom", "paid-on-call",
     "Sandra's current department."),
    ("Town of Westlock", "municipal", "Westlock", "AB",
     "https://www.westlock.ca/careers", "custom", "paid-on-call", ""),
    ("Town of Barrhead", "municipal", "Barrhead", "AB",
     "https://www.barrhead.ca/careers", "custom", "paid-on-call", ""),
    ("Town of Drayton Valley", "municipal", "Drayton Valley", "AB",
     "https://www.draytonvalley.ca/careers", "custom", "both", ""),
    # ── municipal, wider Alberta ──
    ("City of Calgary", "municipal", "Calgary", "AB",
     "https://www.calgary.ca/careers.html", "custom", "full-time",
     "Large intake, competitive. CPAT-style fitness testing."),
    ("City of Red Deer", "municipal", "Red Deer", "AB",
     "https://www.reddeer.ca/city-services/careers/", "custom", "full-time", ""),
    ("City of Airdrie", "municipal", "Airdrie", "AB",
     "https://www.airdrie.ca/careers", "custom", "both", ""),
    ("City of Grande Prairie", "municipal", "Grande Prairie", "AB",
     "https://www.cityofgp.com/city-government/careers", "custom", "full-time", ""),
    ("Regional Municipality of Wood Buffalo", "municipal", "Fort McMurray", "AB",
     "https://www.rmwb.ca/en/careers.aspx", "custom", "full-time",
     "Often hiring; northern premium."),
    ("City of Lethbridge", "municipal", "Lethbridge", "AB",
     "https://www.lethbridge.ca/careers", "custom", "full-time", ""),
    ("City of Medicine Hat", "municipal", "Medicine Hat", "AB",
     "https://www.medicinehat.ca/careers", "custom", "full-time", ""),
    ("City of Cold Lake", "municipal", "Cold Lake", "AB",
     "https://www.coldlake.com/careers", "custom", "both", ""),
    # ── airport / ARFF ──
    ("Edmonton International Airport (YEG)", "airport", "Leduc County", "AB",
     "https://flyeia.com/corporate/careers/", "custom", "full-time",
     "ARFF - aircraft rescue and firefighting."),
    ("Calgary Airport Authority (YYC)", "airport", "Calgary", "AB",
     "https://www.yyc.com/careers", "custom", "full-time", "ARFF."),
    # ── industrial ──
    ("Suncor Energy", "industrial", "Fort McMurray", "AB",
     "https://www.suncor.com/en-ca/careers", "workday", "full-time",
     "Industrial emergency response; strong pay, rotational."),
    ("Canadian Natural (CNRL)", "industrial", "Fort McMurray", "AB",
     "https://www.cnrl.com/careers", "custom", "full-time", ""),
    ("Imperial Oil", "industrial", "Kearl / Cold Lake", "AB",
     "https://www.imperialoil.ca/careers", "workday", "full-time", ""),
    ("Cenovus Energy", "industrial", "Calgary / sites", "AB",
     "https://www.cenovus.com/careers", "workday", "full-time", ""),
    ("Shell Scotford", "industrial", "Fort Saskatchewan", "AB",
     "https://www.shell.ca/en_ca/careers.html", "workday", "full-time", ""),
    ("Dow Canada", "industrial", "Fort Saskatchewan", "AB",
     "https://corporate.dow.com/en-us/careers.html", "workday", "full-time", ""),
    ("Falck Safety Services Canada", "industrial", "Various", "AB",
     "https://www.falck.com/en-ca/careers/", "custom", "full-time",
     "Contract industrial fire and rescue - a common entry route."),
    # ── wildland ──
    ("Alberta Wildfire", "wildland", "Province-wide", "AB",
     "https://www.alberta.ca/wildfire-jobs", "gov-portal", "seasonal",
     "Seasonal; hiring generally opens in winter for spring start."),
    ("BC Wildfire Service", "wildland", "Province-wide", "BC",
     "https://www2.gov.bc.ca/gov/content/safety/wildfire-status/employment", "gov-portal", "seasonal", ""),
    ("Parks Canada", "wildland", "National", "CA",
     "https://parks.canada.ca/agence-agency/emploi-job", "gov-portal", "seasonal", ""),
]


EMPLOYERS_JSON = Path(__file__).resolve().parent / "employers.json"
SOURCES_JSON = Path(__file__).resolve().parent / "sources.json"


DEFAULT_SOURCES = [
    # nursing / care - she has nursing education but is not registered,
    # so these lean to care-aide and support roles she can take today
    ("Job Bank - health care aide, Alberta", "https://www.jobbank.gc.ca/jobsearch/jobsearch?searchstring=health+care+aide&locationstring=Alberta", "healthcare"),
    ("Indeed - care aide, Alberta", "https://ca.indeed.com/jobs?q=health+care+aide&l=Alberta", "healthcare"),
    ("LinkedIn - nursing & care, Alberta", "https://www.linkedin.com/jobs/search/?keywords=nurse%20OR%20care%20aide&location=Alberta%2C%20Canada", "healthcare"),
    ("AHS careers - all health jobs", "https://careers.albertahealthservices.ca/", "healthcare"),
    ("Job Bank - nursing, British Columbia", "https://www.jobbank.gc.ca/jobsearch/jobsearch?searchstring=nurse&locationstring=British+Columbia", "healthcare"),
    ("Indeed - care aide, British Columbia", "https://ca.indeed.com/jobs?q=care+aide&l=British+Columbia", "healthcare"),
    ("Indeed - nursing & care, Toronto", "https://ca.indeed.com/jobs?q=nurse+OR+care+aide&l=Toronto%2C+ON", "healthcare"),
    # firefighter - wide geography
    ("Job Bank - firefighter, Alberta", "https://www.jobbank.gc.ca/jobsearch/jobsearch?searchstring=firefighter&locationstring=Alberta", "fire"),
    ("Job Bank - firefighter, British Columbia", "https://www.jobbank.gc.ca/jobsearch/jobsearch?searchstring=firefighter&locationstring=British+Columbia", "fire"),
    ("Indeed - firefighter, Alberta", "https://ca.indeed.com/jobs?q=firefighter&l=Alberta", "fire"),
    # anything, near home
    ("Indeed - all jobs, Onoway area", "https://ca.indeed.com/jobs?q=&l=Onoway%2C+AB", "general"),
]


def seed_sources() -> int:
    cur = db()
    for name, url, kind in DEFAULT_SOURCES:
        cur.execute("INSERT OR IGNORE INTO sources (name,url,kind) VALUES (?,?,?)",
                    (name, url, kind))
    cur.commit()
    return cur.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"]


def seed_employers() -> int:
    """
    Load the verified employer registry.

    employers.json is the researched source of truth - careers URL, ATS family,
    hiring window, residency rule and whether NFPA is required up front. The
    SEED_EMPLOYERS list below it is only a fallback if that file goes missing.
    """
    cur = db()
    rows = []
    if EMPLOYERS_JSON.exists():
        try:
            data = json.loads(EMPLOYERS_JSON.read_text(encoding="utf-8"))
            for e in data.get("employers", []):
                note = e.get("notes", "")
                extra = []
                if e.get("window_opens"):
                    extra.append(f"Window: {e['window_opens']}")
                if e.get("window_closes"):
                    extra.append(f"Closes: {e['window_closes']}")
                if e.get("residency_rule"):
                    extra.append(f"Residency: {e['residency_rule']}")
                if e.get("stations"):
                    extra.append(f"Stations: {e['stations']}")
                if extra:
                    note = " | ".join(extra) + "\n" + note
                rows.append((e["name"], e.get("kind"), e.get("city"), e.get("province"),
                             e.get("careers_url"), e.get("ats"), e.get("hires"), note))
        except (json.JSONDecodeError, KeyError) as exc:
            print(f"  ! employers.json unreadable ({exc}), falling back", file=sys.stderr)

    if not rows:
        rows = list(SEED_EMPLOYERS)

    for row in rows:
        try:
            cur.execute(
                """INSERT OR IGNORE INTO employers
                   (name, kind, city, province, careers_url, ats, hires, notes)
                   VALUES (?,?,?,?,?,?,?,?)""", row)
        except sqlite3.Error as e:
            print("seed error", row[0], e, file=sys.stderr)
    cur.commit()
    count = cur.execute("SELECT COUNT(*) c FROM employers").fetchone()["c"]
    log_event(f"Employer registry loaded - {count} employers on watch")
    return count


# ─────────────────────── resume parsing ────────────────────────
# Known credentials, so the tool can lift them straight off the resume
# instead of making her retype what is already there.
KNOWN_CERTS = [
    ("NFPA 1001 Level II", r"nfpa\s*1001.*(level\s*(ii|2)|firefighter\s*(ii|2))"),
    ("NFPA 1001 Level I", r"nfpa\s*1001"),
    ("NFPA 1002 Driver/Operator", r"nfpa\s*1002|driver[\s/-]*operator|pump\s*operator"),
    ("NFPA 1072 HazMat", r"nfpa\s*1072|haz\s*mat|hazardous\s*materials"),
    ("NFPA 1006 Rescue", r"nfpa\s*1006|technical\s*rescue"),
    ("Emergency Medical Responder (EMR)", r"\bemr\b|emergency\s*medical\s*responder"),
    ("Primary Care Paramedic (PCP)", r"\bpcp\b|primary\s*care\s*paramedic"),
    ("Advanced Care Paramedic (ACP)", r"\bacp\b|advanced\s*care\s*paramedic"),
    ("Registered Nurse (RN)", r"registered\s*nurse|\brn\b|bscn|\bbn\b"),
    ("Licensed Practical Nurse (LPN)", r"licensed\s*practical\s*nurse|\blpn\b"),
    ("Health Care Aide", r"health\s*care\s*aide|\bhca\b|care\s*aide"),
    ("Standard First Aid + CPR-C", r"first\s*aid|\bcpr\b"),
    ("Basic Life Support (BLS)", r"\bbls\b|basic\s*life\s*support"),
    ("ICS 100", r"ics[\s-]*100"),
    ("S-100 Wildland", r"\bs-?100\b"),
    ("H2S Alive", r"h2s\s*alive"),
    ("Confined Space", r"confined\s*space"),
    ("WHMIS", r"whmis"),
    ("Class 1 licence", r"class\s*1\b"),
    ("Class 3 licence", r"class\s*3\b"),
    ("Class 4 licence", r"class\s*4\b"),
    ("Air brakes (Q endorsement)", r"air\s*brake|\bq\s*endorsement\b"),
]


def resume_text(path: Path) -> str:
    """Best-effort plain text from a .docx, .txt or .pdf resume."""
    suf = path.suffix.lower()
    try:
        if suf == ".docx":
            import zipfile, xml.etree.ElementTree as ET
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf-8", "ignore")
            xml = re.sub(r"</w:p>", "\n", xml)
            return re.sub(r"<[^>]+>", " ", xml)
        if suf in (".txt", ".rtf", ".md"):
            return path.read_text(encoding="utf-8", errors="ignore")
        if suf == ".pdf":
            raw = path.read_bytes().decode("latin-1", "ignore")
            # crude but dependency-free: pull text between BT/ET or parens
            chunks = re.findall(r"\(([^)]{2,})\)", raw)
            return " ".join(chunks)
    except Exception:
        # Deliberately broad: this parses arbitrary bytes a person uploaded,
        # in three different formats, on a best-effort basis. A malformed or
        # unusual file (BadZipFile on a corrupted/converted .docx, a
        # SyntaxError-derived ParseError on odd XML, anything else) must fall
        # through to the "" fallback below, never take down the request that
        # is also responsible for writing the file and recording it - a résumé
        # that merely fails to parse must still upload successfully.
        pass
    return ""


# What an application actually asks for, and the plain question to ask her.
# Only these are chased - nothing invented, nothing decorative.
REQUIRED_FIELDS = [
    ("first_name",  "What is your legal first name?"),
    ("last_name",   "And your last name?"),
    ("email",       "What email should employers use?"),
    ("phone",       "What phone number?"),
    ("city",        "What town or city do you live in?"),
    ("province",    "Which province?"),
    ("address",     "What is your street address? Applications ask for it."),
    ("postal",      "What is your postal code?"),
    ("work_auth",   "Are you a Canadian citizen, a permanent resident, or on a work permit?"),
    ("licence_class", "What class is your driver's licence, and do you have air brakes?"),
    ("relocate",    "Would you relocate for the right job, or stay near Onoway?"),
    ("available_from", "When could you start?"),
    ("crc",         "Do you have a current criminal record check with vulnerable sector?"),
    ("ref1_name",   "Who is one work reference - name and how to reach them?"),
]


def profile_from_resume(path: Path) -> int:
    """Pull the obvious identity details straight off the résumé."""
    text = resume_text(path)
    if not text:
        return 0
    flat = re.sub(r"[ \t]+", " ", text)
    found = {}

    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", flat)
    if m:
        found["email"] = m.group(0).strip(".,;")

    m = re.search(r"(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", flat)
    if m:
        found["phone"] = m.group(0).strip()

    m = re.search(r"\b([A-Z]\d[A-Z])[\s-]?(\d[A-Z]\d)\b", flat)
    if m:
        found["postal"] = f"{m.group(1)} {m.group(2)}"

    for prov, full in [("Alberta", "Alberta"), ("British Columbia", "British Columbia"),
                       ("\bAB\b", "Alberta"), ("\bBC\b", "British Columbia"),
                       ("Ontario", "Ontario"), ("Saskatchewan", "Saskatchewan")]:
        if re.search(prov, flat):
            found["province"] = full
            break

    for city in ["Onoway", "Lac Ste. Anne", "Sangudo", "Mayerthorpe", "Edmonton",
                 "Spruce Grove", "Stony Plain", "Calgary", "Vancouver", "Toronto"]:
        if re.search(re.escape(city), flat, re.I):
            found["city"] = city
            break

    # name: first non-empty line that looks like a person, not a heading
    for line in [l.strip() for l in text.splitlines() if l.strip()][:6]:
        if "@" in line or re.search(r"\d{3}", line):
            continue
        words = [w for w in re.split(r"[\s,|]+", line) if w]
        if 2 <= len(words) <= 4 and all(re.match(r"^[A-Z][A-Za-z.'-]*$", w) for w in words):
            found["first_name"] = words[0].rstrip(".")
            found["last_name"] = words[-1]
            break

    n = 0
    for k, v in found.items():
        cur = db().execute("SELECT value FROM profile WHERE key=?", (k,)).fetchone()
        if cur and str(cur["value"]).strip():
            continue                      # never overwrite what she already said
        db().execute("INSERT INTO profile (key,value) VALUES (?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
        n += 1
    if n:
        db().commit()
        log_event(f"Read {n} details off the résumé")
    return n


def lift_certs_from_resume(path: Path) -> int:
    text = resume_text(path)
    if not text:
        return 0
    low = text.lower()
    found = 0
    for name, pat in KNOWN_CERTS:
        if re.search(pat, low):
            # do not duplicate one already recorded
            exists = db().execute(
                "SELECT 1 FROM certs WHERE name=?", (name,)).fetchone()
            if exists:
                continue
            db().execute(
                "INSERT INTO certs (name,status,source) VALUES (?,?,'resume')",
                (name, "On resume"))
            found += 1
    if found:
        db().commit()
        log_event(f"Lifted {found} certifications from the resume")
    return found


# ─────────────────────── match scoring ─────────────────────────
CERT_WEIGHT = {
    "nfpa 1001": 40, "emr": 20, "primary care paramedic": 25,
    "class 3": 12, "air brakes": 8, "nfpa 1002": 10,
    "nfpa 1072": 8, "ics": 4, "first aid": 6, "s-100": 10,
}


def score_posting(title: str, employment_type: str) -> int:
    """Rough fit score from the profile's certifications."""
    certs = [r["name"].lower() for r in
             db().execute("SELECT name FROM certs WHERE status IN ('Complete','In progress')")]
    score = 30  # baseline: she is a serving firefighter
    for key, w in CERT_WEIGHT.items():
        if any(key in c for c in certs):
            score += w
    t = (title or "").lower()
    if "firefighter" in t:
        score += 15
    if "paid" in t or "casual" in t or "volunteer" in t:
        score += 5
    return max(0, min(100, score))


# ───────────────────────── HTTP layer ──────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = f"Muster/{VERSION}"
    # HTTP/1.0 (the stdlib default) tears down and re-opens a fresh TCP
    # connection for every single request. At a health poll every 10s, plus
    # the front-end's own 20s ping, plus normal use, that is a lot of churn
    # on localhost - and Windows occasionally stalls a brand-new local
    # connection attempt for a few seconds under that kind of load, which
    # looks exactly like "the server died" to a naive timeout even though
    # the process is fine. HTTP/1.1 lets a client keep one connection open;
    # _send() already always sets Content-Length, which keep-alive requires
    # to know where a response ends.
    protocol_version = "HTTP/1.1"

    # -- plumbing --
    def _cors(self, origin: str | None) -> None:
        allow = origin if origin in ALLOW_ORIGINS else ALLOW_ORIGINS[0]
        if origin and (origin.startswith("http://localhost")
                       or origin.startswith("http://127.0.0.1")
                       or origin.endswith(".github.io")):
            allow = origin
        self.send_header("Access-Control-Allow-Origin", allow)
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Muster-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _send(self, obj, code=200) -> None:
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors(self.headers.get("Origin"))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    # ── who is allowed to talk to this engine ──────────────────────────
    #
    # Two rules, checked in this order, and the first one is not negotiable:
    #
    #   1. The caller's IP must be on a private network - this machine, or
    #      something on the same home LAN. A public address is refused
    #      outright, PIN or no PIN. A home router will not route outside
    #      traffic inward on its own, but that is the router's promise, not
    #      ours; if it is ever misconfigured, port-forwarded by accident or
    #      exposed by UPnP, this check still holds. Defence that does not
    #      depend on someone else's settings being right.
    #
    #   2. Anything arriving over the network - i.e. not from this machine -
    #      has to have registered once with the PIN. This machine itself is
    #      trusted without a PIN: whoever is sitting at it can open the files
    #      directly anyway, so a prompt would be theatre.
    #
    # This engine runs the assistant and rewrites its own front-end, so the
    # blast radius of a stranger reaching it is real. Hence refusing by
    # default and opening up deliberately, rather than the other way round.

    def _send_unlock(self) -> None:
        """The one screen a new device sees before anything else."""
        page = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Muster</title><style>
:root{color-scheme:light dark}
body{margin:0;min-height:100vh;display:grid;place-items:center;
 font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
 background:#FFF8F0;color:#2B1B2E}
@media (prefers-color-scheme:dark){body{background:#1A1020;color:#FCEEF6}}
.box{width:min(92vw,380px);padding:32px;border-radius:22px;background:#fff;
 box-shadow:0 18px 44px rgba(214,16,106,.14);text-align:center}
@media (prefers-color-scheme:dark){.box{background:#241630}}
h1{font-size:22px;margin:0 0 6px}p{color:#6B5570;font-size:14.5px;margin:0 0 20px}
@media (prefers-color-scheme:dark){p{color:#C4A9CE}}
input{width:100%;box-sizing:border-box;padding:13px;font-size:19px;text-align:center;
 letter-spacing:.3em;border:1px solid #EBD9CE;border-radius:14px;background:transparent;
 color:inherit;margin-bottom:12px}
button{width:100%;padding:13px;font-size:15px;font-weight:600;border:0;cursor:pointer;
 border-radius:99px;background:#FF2E88;color:#fff}
.err{color:#D94F4F;font-size:13.5px;min-height:19px;margin-top:10px}
</style></head><body><div class="box">
<div style="font-size:34px">&#128274;</div>
<h1>New device</h1>
<p>Enter the PIN from Muster to use it on this device. You will only be asked once.</p>
<input id="pin" type="text" inputmode="numeric" autocomplete="one-time-code"
       placeholder="PIN" autofocus>
<button id="go">Unlock</button>
<div class="err" id="err"></div>
</div><script>
const go=document.getElementById('go'),pin=document.getElementById('pin'),err=document.getElementById('err');
async function submit(){
  err.textContent='';go.disabled=true;
  try{
    const r=await fetch('/device/register',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({pin:pin.value,name:navigator.userAgent.slice(0,60)})});
    const d=await r.json();
    if(d.ok){location.reload();return;}
    err.textContent=d.error||'That PIN did not work.';
  }catch(e){err.textContent='Could not reach Muster.';}
  go.disabled=false;pin.value='';
}
go.onclick=submit;
pin.addEventListener('keydown',e=>{if(e.key==='Enter')submit();});
</script></body></html>"""
        body = page.encode("utf-8")
        self.send_response(401)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _register_device(self, b: dict) -> None:
        """Trade the right PIN for a token this device keeps."""
        ip = self._client_ip()
        if self._ip_kind() == "outside":
            return self._send({"error": "outside the home network"}, 403)
        if not ACCESS_PIN:
            return self._send({"error": "no ACCESS_PIN is set in .env"}, 403)

        # Slow brute force to a crawl. A PIN is short by design, so without
        # this a script on the LAN could walk the whole space in seconds.
        now = time.time()
        tries = [t for t in _PIN_TRIES.get(ip, []) if now - t < 300]
        if len(tries) >= 5:
            wait = int(300 - (now - tries[0]))
            _PIN_TRIES[ip] = tries
            return self._send({"error": f"Too many tries. Wait {wait}s."}, 429)

        if not hmac.compare_digest(str(b.get("pin", "")).strip(), ACCESS_PIN):
            tries.append(now)
            _PIN_TRIES[ip] = tries
            log_event(f"Wrong PIN from {ip} on the home network")
            return self._send({"error": "That PIN did not work."}, 403)

        _PIN_TRIES.pop(ip, None)
        token = secrets.token_urlsafe(32)
        name = str(b.get("name", "")).strip()[:80] or "a device"
        db().execute("INSERT INTO devices (token,name,ip) VALUES (?,?,?)",
                     (token, name, ip))
        db().commit()
        log_event(f"New device registered on the home network: {name} ({ip})")

        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # A year, so she is not asked again. Lax keeps it off cross-site
        # requests; the cookie is useless to another origin anyway.
        self.send_header("Set-Cookie",
                         f"muster_device={token}; Max-Age=31536000; Path=/; SameSite=Lax")
        self.end_headers()
        self.wfile.write(body)

    def _client_ip(self) -> str:
        return self.client_address[0] if self.client_address else ""

    def _ip_kind(self) -> str:
        """'self' (this machine), 'lan' (same home network), or 'outside'."""
        try:
            addr = ipaddress.ip_address(self._client_ip())
        except ValueError:
            return "outside"
        if addr.is_loopback:
            return "self"
        return "lan" if any(addr in net for net in HOME_NETWORKS) else "outside"

    def _device_token(self) -> str:
        raw = self.headers.get("Cookie")
        if not raw:
            return ""
        try:
            jar = http.cookies.SimpleCookie()
            jar.load(raw)
        except http.cookies.CookieError:
            return ""
        m = jar.get("muster_device")
        return m.value if m else ""

    def _known_device(self) -> bool:
        tok = self._device_token()
        if not tok:
            return False
        row = db().execute("SELECT id FROM devices WHERE token=?", (tok,)).fetchone()
        if not row:
            return False
        db().execute("UPDATE devices SET last_seen=CURRENT_TIMESTAMP, ip=? WHERE id=?",
                     (self._client_ip(), row["id"]))
        db().commit()
        return True

    def _network_gate(self, path: str) -> bool:
        """True to continue. Otherwise a response has already been written."""
        kind = self._ip_kind()

        if kind == "outside":
            log_event(f"Refused a connection from outside the home network ({self._client_ip()})")
            self._send({"error": "Muster only answers devices on your own home network."}, 403)
            return False

        if kind == "self" or path in ("/health", "/device/register"):
            return True

        if self._known_device():
            return True

        if not ACCESS_PIN:
            # Reachable over the LAN but no PIN was ever set. Refuse rather
            # than quietly serving everything to the whole network - an open
            # door nobody chose is worse than one that will not open yet.
            self._send({"error": "This device is not registered, and no ACCESS_PIN "
                                 "is set in .env for it to register with."}, 403)
            return False

        # A browser asking for a page gets the unlock screen; anything else
        # (a fetch from our own JS) gets a 401 it can act on.
        if self.command == "GET" and "text/html" in (self.headers.get("Accept") or ""):
            self._send_unlock()
        else:
            self._send({"error": "unregistered device", "needs_pin": True}, 401)
        return False

    def _authed(self) -> bool:
        """
        The token exists to stop OTHER websites driving this engine. It is not
        needed for the page the engine served itself: a same-origin request
        already proves it came from our own UI. So local use needs no token,
        and only genuine cross-origin callers have to present one.
        """
        if not TOKEN:
            return True

        origin = self.headers.get("Origin")
        if not origin:
            # No Origin header - a same-origin GET, or a non-browser client
            # on the loopback interface. Both are ours.
            return True

        try:
            host = origin.split("//", 1)[1]
        except IndexError:
            host = ""
        if host in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
            return True

        return self.headers.get("X-Muster-Token") == TOKEN

    def log_message(self, fmt, *args):
        print(f"  {self.command} {self.path}")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors(self.headers.get("Origin"))
        self.end_headers()

    # -- routes --
    # -- static front-end --
    # Serving docs/ from this same origin is what makes the app reliable.
    # A public HTTPS page calling http://127.0.0.1 gets blocked by extensions,
    # Private Network Access rules and mixed-content policy; same-origin has
    # none of those problems.
    MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8", ".json": "application/json",
            ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}

    def _serve_static(self, path: str) -> bool:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_DIR / rel).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())   # no path traversal
        except ValueError:
            return False
        if not target.is_file():
            return False
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", self.MIME.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)
        return True

    def _safe_error(self, e: Exception) -> None:
        """
        Last-resort net. An unhandled exception partway through a handler -
        exactly what happened tonight when a résumé that failed to parse
        crashed the whole /upload request - otherwise breaks the HTTP
        response with no error the browser can show and no server log
        explaining why. This guarantees SOME answer comes back, and that the
        real cause is visible in server.err instead of silence.
        """
        import traceback
        traceback.print_exc()
        try:
            log_event(f"Request error on {self.path[:60]}: {type(e).__name__}: {e}")
        except sqlite3.Error:
            pass
        try:
            self._send({"error": f"Something went wrong on this request "
                                 f"({type(e).__name__}). Nothing was lost - "
                                 "try again."}, 500)
        except (BrokenPipeError, ConnectionAbortedError, OSError):
            pass   # the client already gave up; nothing left to send to

    def do_GET(self):
        try:
            self._do_GET(self.path.split("?")[0])
        except Exception as e:
            self._safe_error(e)

    def do_POST(self):
        try:
            self._do_POST()
        except Exception as e:
            self._safe_error(e)

    def _do_GET(self, p):
        if not self._network_gate(p):
            return
        if p == "/" or not p.startswith(("/health", "/profile", "/certs", "/employers",
                                         "/postings", "/applications", "/stats", "/scan",
                                         "/chat", "/upload")):
            if self._serve_static(p):
                return
        if p == "/health":
            return self._send({"ok": True, "version": VERSION,
                               "gmail": bool(ENV.get("GMAIL_APP_PASSWORD")),
                               "chat": ENV.get("CHAT_PROVIDER", "ollama")})
        if not self._authed():
            return self._send({"error": "bad token"}, 401)

        if p == "/profile":
            rows = db().execute("SELECT key, value FROM profile").fetchall()
            return self._send({r["key"]: r["value"] for r in rows})

        if p == "/certs":
            return self._send([dict(r) for r in
                               db().execute("SELECT * FROM certs ORDER BY id DESC")])

        if p == "/employers":
            return self._send([dict(r) for r in
                               db().execute("SELECT * FROM employers ORDER BY name")])

        if p == "/postings":
            rows = db().execute("""
                SELECT p.*, e.name AS employer, e.ats
                FROM postings p LEFT JOIN employers e ON e.id = p.employer_id
                WHERE p.active = 1 ORDER BY p.match DESC, p.first_seen DESC""").fetchall()
            return self._send([dict(r) for r in rows])

        if p == "/applications":
            rows = db().execute("""
                SELECT a.*, p.title, e.name AS employer
                FROM applications a
                LEFT JOIN postings p ON p.id = a.posting_id
                LEFT JOIN employers e ON e.id = p.employer_id
                ORDER BY a.id DESC""").fetchall()
            return self._send([dict(r) for r in rows])

        if p == "/sources":
            return self._send([dict(r) for r in
                               db().execute("SELECT * FROM sources ORDER BY id DESC")])

        if p == "/profile/gaps":
            prof = {r["key"]: (r["value"] or "").strip()
                    for r in db().execute("SELECT key,value FROM profile")}
            gaps = [{"key": k, "question": q}
                    for k, q in REQUIRED_FIELDS if not prof.get(k)]
            have = [{"key": k, "value": prof[k]}
                    for k, _ in REQUIRED_FIELDS if prof.get(k)]
            return self._send({"have": have, "gaps": gaps,
                               "complete": len(have), "total": len(REQUIRED_FIELDS)})

        if p == "/stats":
            return self._send(self._stats())

        if p == "/email/status":
            return self._send({"connected": bool(ENV.get("GMAIL_APP_PASSWORD")),
                               "address": ENV.get("GMAIL_ADDRESS", "")})

        if p == "/inbox":
            import email_client
            return self._send(email_client.scan_inbox(
                ENV.get("GMAIL_ADDRESS", ""), ENV.get("GMAIL_APP_PASSWORD", "")))

        return self._send({"error": "not found"}, 404)

    def _do_POST(self):
        p = self.path.split("?")[0]
        if not self._network_gate(p):
            return
        b = self._body()

        if p == "/device/register":
            return self._register_device(b)

        if not self._authed():
            return self._send({"error": "bad token"}, 401)

        if p == "/profile":
            for k, v in b.items():
                db().execute(
                    "INSERT INTO profile (key,value) VALUES (?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
            db().commit()
            log_event("Profile updated")
            return self._send({"ok": True, "fields": len(b)})

        if p == "/certs":
            db().execute("INSERT INTO certs (name,status,expiry) VALUES (?,?,?)",
                         (b.get("name"), b.get("status"), b.get("expiry")))
            db().commit()
            log_event(f"Certification recorded: {b.get('name')}")
            return self._send({"ok": True})

        if p == "/upload":
            kind = b.get("kind", "documents")
            fname = re.sub(r"[^A-Za-z0-9._-]", "_", b.get("filename", "file"))
            target = (RESUME_DIR if kind == "resumes" else DOCS_DIR) / fname
            try:
                target.write_bytes(base64.b64decode(b.get("content_b64", "")))
            except Exception as e:
                return self._send({"error": f"decode failed: {e}"}, 400)
            db().execute("INSERT INTO documents (kind,filename,path) VALUES (?,?,?)",
                         (kind, fname, str(target)))
            db().commit()
            log_event(f"Document stored: {fname}")
            lifted = fields = 0
            if kind == "resumes":
                lifted = lift_certs_from_resume(target)
                fields = profile_from_resume(target)
            return self._send({"ok": True, "path": str(target),
                               "lifted_certs": lifted, "lifted_fields": fields})

        if p == "/sources/chat":
            return self._send(self._sources_chat(b.get("message", "")))

        if p == "/sources":
            try:
                db().execute(
                    "INSERT OR IGNORE INTO sources (name,url,kind) VALUES (?,?,?)",
                    (b.get("name"), b.get("url"), b.get("kind", "general")))
                db().commit()
            except sqlite3.Error as e:
                return self._send({"error": str(e)}, 400)
            log_event(f"Watching a new place: {b.get('name')}")
            return self._send({"ok": True})

        if p == "/certs/delete":
            db().execute("DELETE FROM certs WHERE id=?", (b.get("id"),))
            db().commit()
            return self._send({"ok": True})

        if p == "/schedule":
            hrs = int(b.get("hours", 0) or 0)
            db().execute("INSERT INTO profile (key,value) VALUES ('scan_every_hours',?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(hrs),))
            db().commit()
            log_event(f"Automatic job search set to every {hrs}h" if hrs else "Automatic job search turned off")
            return self._send({"ok": True, "hours": hrs})

        if p == "/sources/delete":
            db().execute("DELETE FROM sources WHERE id=?", (b.get("id"),))
            db().commit()
            return self._send({"ok": True})

        if p == "/profile/chat":
            return self._send(self._profile_chat(b.get("message", "")))

        if p == "/apply/start":
            return self._send(self._apply_start(b.get("url", "")))

        if p == "/email/connect":
            return self._send(self._email_connect(
                b.get("address", ""), b.get("app_password", "")))

        if p == "/upgrade":
            return self._send(self._upgrade(b.get("request", "")))

        if p == "/upgrade/undo":
            return self._send(self._upgrade_undo())

        if p == "/upgrade/reset":
            return self._send(self._upgrade_reset())

        if p == "/upgrade/selftest":
            return self._send(self._self_test())

        if p == "/employers/seed":
            return self._send({"ok": True, "count": seed_employers()})

        if p == "/scan":
            return self._send(self._scan())

        if p == "/chat":
            return self._send({"reply": self._chat(b.get("message", ""),
                                                   b.get("history", []),
                                                   b.get("context", "general"))})

        return self._send({"error": "not found"}, 404)

    # -- logic --
    def _stats(self) -> dict:
        c = db()
        open_n = c.execute("SELECT COUNT(*) n FROM postings WHERE active=1").fetchone()["n"]
        sent = c.execute("SELECT COUNT(*) n FROM applications WHERE status='submitted'").fetchone()["n"]
        replies = c.execute("SELECT COUNT(*) n FROM applications WHERE status='replied'").fetchone()["n"]

        deadlines, soon = [], 0
        today = date.today()
        for r in c.execute("""SELECT p.closes, p.title, e.name AS employer
                              FROM postings p LEFT JOIN employers e ON e.id=p.employer_id
                              WHERE p.closes IS NOT NULL AND p.active=1"""):
            try:
                d = (datetime.fromisoformat(r["closes"]).date() - today).days
            except (TypeError, ValueError):
                continue
            if d <= 14:
                soon += 1
            deadlines.append({"days": d, "what": r["title"], "who": r["employer"]})

        for r in c.execute("SELECT name, expiry FROM certs WHERE expiry IS NOT NULL"):
            try:
                d = (datetime.fromisoformat(r["expiry"]).date() - today).days
            except (TypeError, ValueError):
                continue
            if d < 120:
                deadlines.append({"days": d, "what": f"{r['name']} expires",
                                  "who": "Certification renewal"})
        deadlines.sort(key=lambda x: x["days"])

        activity = [{"text": r["text"], "at": r["at"]} for r in
                    c.execute("SELECT text, at FROM events ORDER BY id DESC LIMIT 8")]

        return {"open": open_n, "sent": sent, "replies": replies,
                "closing_soon": soon, "deadlines": deadlines[:8], "activity": activity}

    def _scan(self) -> dict:
        """
        Fetch each employer's careers page and look for firefighter postings.

        Deliberately conservative: it records candidate pages for review rather
        than inventing structured postings it cannot actually parse. Per-ATS
        adapters (NEOGOV / Workday) land in M3.
        """
        found = 0
        checked = 0
        skipped = 0
        total = db().execute("SELECT COUNT(*) c FROM employers").fetchone()["c"]
        custom = db().execute("SELECT COUNT(*) c FROM sources").fetchone()["c"]
        pat = re.compile(r"fire\s*fighter|firefighter|fire\s+services|emergency\s+response",
                         re.I)

        # Aggregators are polled only where robots.txt permits it. Several major
        # boards (Indeed, LinkedIn, Glassdoor, CivicInfo BC) explicitly disallow
        # automated job tools, so they are recorded and deliberately never fetched.
        srcs = SOURCES_JSON
        if srcs.exists():
            try:
                for s in json.loads(srcs.read_text(encoding="utf-8")).get("sources", []):
                    if not s.get("allowed"):
                        skipped += 1
                        continue
                    db().execute(
                        """INSERT OR IGNORE INTO employers
                           (name, kind, city, province, careers_url, ats, hires, notes)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (s["name"], "aggregator", "-", "CA", s["url"], "feed", "varies",
                         f"Crawl delay {s.get('crawl_delay', 5)}s. {s.get('notes', '')}"))
                db().commit()
            except (json.JSONDecodeError, KeyError, sqlite3.Error):
                pass
        for src in db().execute("SELECT * FROM sources").fetchall():
            db().execute(
                """INSERT OR IGNORE INTO employers
                   (name, kind, city, province, careers_url, ats, hires, notes)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (src["name"], src["kind"] or "general", "-", "-",
                 src["url"], "custom", "varies", "Added by you as a place to look."))
        db().commit()

        # cap per run so the button never spins for minutes
        rows = db().execute("SELECT * FROM employers ORDER BY id DESC LIMIT 14").fetchall()
        for e in rows:
            url = e["careers_url"]
            if not url:
                continue
            checked += 1
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0 (Muster job watcher)"})
                with urllib.request.urlopen(req, timeout=6) as resp:
                    html = resp.read(400_000).decode("utf-8", "ignore")
            except (urllib.error.URLError, OSError, TimeoutError):
                continue

            if not pat.search(html):
                continue
            title = f"Firefighter opportunities - {e['name']}"
            match = score_posting(title, e["hires"])
            try:
                db().execute(
                    """INSERT OR IGNORE INTO postings
                       (employer_id,title,url,employment_type,city,match)
                       VALUES (?,?,?,?,?,?)""",
                    (e["id"], title, url, e["hires"], e["city"], match))
                if db().total_changes:
                    found += 1
            except sqlite3.Error:
                pass
        db().commit()
        log_event(f"Scan complete - {checked} sources checked, {found} new leads, {skipped} skipped (robots.txt)")
        return {"ok": True, "checked": checked, "new": found,
                "skipped_by_robots": skipped, "employers": total, "custom_sources": custom}

    EDITABLE = {"styles.css", "index.html", "app.js"}

    def _sources_chat(self, message: str) -> dict:
        """
        Turn plain English - "LinkedIn nursing jobs in Alberta" - into an
        actual source and add it. She never types a web address.
        """
        system = (
            "You help Sandra choose PLACES to look for work and add them to "
            "her job-search tool. She lives near Onoway, Alberta. She is "
            "interested in fire service and healthcare work, and general jobs "
            "too.\n\n"
            "When she names somewhere to look, reply in ONE short friendly "
            "sentence, then on a NEW LINE output a JSON object of what to add:\n"
            '{\"add\":[{\"name\":\"LinkedIn - nursing, Alberta\",'
            '\"url\":\"https://www.linkedin.com/jobs/search/?keywords=nurse&location=Alberta%2C%20Canada\",'
            '\"kind\":\"healthcare\"}]}\n\n'
            "Rules:\n"
            "- Give REAL, working URLs in the site's normal job-search format. "
            "Never invent a domain. For big sites use their real search URLs "
            "(LinkedIn, Indeed, Job Bank, AHS careers, a city careers page).\n"
            "- kind is exactly one of: fire, healthcare, general.\n"
            "- If she asks a broad 'where should I look' question, suggest two "
            "to four good places and put them all in the JSON.\n"
            "- The JSON goes on its own line, no code fences."
        )
        try:
            raw = _chat_claude_cli(system, [], message)
        except (RuntimeError, OSError) as e:
            return {"reply": f"I could not reach the assistant ({e}).", "added": []}

        reply, added = raw.strip(), []
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict) and "add" in data:
                reply = raw[:m.start()].strip() or "Done - added below."
                for src in data.get("add", []):
                    url = str(src.get("url", "")).strip()
                    if not url.startswith("http"):
                        continue
                    name = str(src.get("name") or url).strip()
                    kind = src.get("kind", "general")
                    if kind not in ("fire", "healthcare", "general"):
                        kind = "general"
                    try:
                        db().execute(
                            "INSERT OR IGNORE INTO sources (name,url,kind) VALUES (?,?,?)",
                            (name, url, kind))
                        added.append({"name": name, "url": url, "kind": kind})
                    except sqlite3.Error:
                        pass
                db().commit()
                if added:
                    log_event(f"Added {len(added)} place(s) to look, via chat")
        return {"reply": reply, "added": added}

    def _profile_chat(self, message: str) -> dict:
        """She answers in plain words; we work out which field it fills."""
        prof = {r["key"]: (r["value"] or "") for r in
                db().execute("SELECT key,value FROM profile")}
        gaps = [(k, q) for k, q in REQUIRED_FIELDS if not prof.get(k, "").strip()]
        gap_list = "\n".join(f"- {k}: {q}" for k, q in gaps) or "(nothing missing)"

        system = (
            "You are helping Sandra fill in the details a job application needs. "
            "She just said something. Work out which of these outstanding fields "
            "her answer fills, if any.\n\n"
            f"Outstanding fields:\n{gap_list}\n\n"
            "Reply with ONE short friendly sentence confirming what you recorded, "
            "then on a NEW LINE a JSON object of the fields to save, e.g.\n"
            '{"set": {"city": "Onoway", "province": "Alberta"}}\n\n'
            "Use only keys from the list above. If her message answers nothing, "
            "reply normally and use {\"set\": {}}. Never invent a value."
        )
        try:
            raw = _chat_claude_cli(system, [], message)
        except (RuntimeError, OSError) as e:
            return {"reply": f"Could not reach the assistant ({e}).", "saved": {}}

        reply, saved = raw.strip(), {}
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict) and isinstance(data.get("set"), dict):
                reply = raw[:m.start()].strip() or "Saved."
                valid = {k for k, _ in REQUIRED_FIELDS}
                for k, v in data["set"].items():
                    if k in valid and str(v).strip():
                        db().execute(
                            "INSERT INTO profile (key,value) VALUES (?,?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (k, str(v).strip()))
                        saved[k] = str(v).strip()
                if saved:
                    db().commit()
                    log_event(f"Profile: recorded {', '.join(saved)}")
        return {"reply": reply, "saved": saved}

    def _apply_start(self, url: str) -> dict:
        """
        Open the posting and fill it in a real browser, ready for review.

        Every attempt is recorded, including the ones that did not go
        cleanly - a hard failure or a login/CAPTCHA wall - so nothing just
        vanishes. Sandra sees a 'needs attention' row with the reason
        instead of silence.
        """
        if not url.startswith("http"):
            return {"error": "That does not look like a web address."}
        import applier
        profile = {r["key"]: r["value"] for r in
                   db().execute("SELECT key,value FROM profile")}
        resume = db().execute(
            "SELECT path FROM documents WHERE kind='resumes' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        resume_path = resume["path"] if resume else None
        headless = ENV.get("HEADLESS", "false").lower() == "true"

        try:
            rep = applier.fill_application(url, profile, resume_path,
                                           channel=ENV.get("BROWSER_CHANNEL", "msedge"),
                                           headless=headless)
        except Exception as e:
            rep = {"ok": False, "error": f"Could not run the browser: {e}",
                   "filled": [], "notes": []}

        pid = db().execute(
            "INSERT INTO postings (title,url,active) VALUES (?,?,0) "
            "ON CONFLICT(url) DO NOTHING", (url[:120], url)).lastrowid
        if not pid:
            row = db().execute("SELECT id FROM postings WHERE url=?", (url,)).fetchone()
            pid = row["id"] if row else None

        if not rep.get("ok"):
            reason = rep.get("error", "Unknown error")
            db().execute(
                "INSERT INTO applications (posting_id,status,notes) VALUES (?,'failed',?)",
                (pid, f"Could not apply at {url} - {reason}"))
            db().commit()
            log_event(f"Application FAILED at {url[:60]} - needs your attention")
        elif rep.get("notes"):
            reason = " ".join(rep["notes"])
            db().execute(
                "INSERT INTO applications (posting_id,status,notes) VALUES (?,'needs_you',?)",
                (pid, f"{url} - {reason}"))
            db().commit()
            log_event(f"Application at {url[:60]} needs you - {reason[:60]}")
        else:
            db().execute(
                "INSERT INTO applications (posting_id,status,notes) VALUES (?,'review',?)",
                (pid, f"Auto-filled {url} - {len(rep.get('filled', []))} fields"))
            db().commit()
            log_event(f"Filled an application at {url[:60]} - review it")
        return rep

    def _email_connect(self, address: str, app_password: str) -> dict:
        import email_client
        ok, msg = email_client.test_login(address, app_password)
        if not ok:
            return {"ok": False, "message": msg}
        # persist to .env (gitignored, never served)
        env_path = ROOT / ".env"
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        def setline(key, val):
            for i, ln in enumerate(lines):
                if ln.strip().startswith(key + "="):
                    lines[i] = f"{key}={val}"; return
            lines.append(f"{key}={val}")
        setline("GMAIL_ADDRESS", address)
        setline("GMAIL_APP_PASSWORD", app_password.replace(" ", ""))
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ENV["GMAIL_ADDRESS"] = address
        ENV["GMAIL_APP_PASSWORD"] = app_password.replace(" ", "")
        log_event("Email connected")
        return {"ok": True, "message": msg}

    def _upgrade(self, request: str) -> dict:
        """
        Let the assistant rewrite its own front-end.

        Only the two files that define how the page looks and reads are in
        scope, a timestamped backup is taken first, and the result must still
        look like the right kind of file before it is written.
        """
        if not request.strip():
            return {"error": "Say what you would like changed."}

        # Three kinds of request, three files. Getting this wrong is not
        # cosmetic: a behaviour bug routed to CSS becomes a doomed rewrite
        # that can only fail, which is what made self-upgrade look broken
        # in the first place.
        #
        # Order is not simply "behaviour first" - "fix the typo in the
        # title" contains 'fix' but is a wording change, so unambiguous
        # wording words are checked before behaviour gets a turn. Softer
        # wording words (message, text) are checked last, after behaviour,
        # since "the message does not send" is a behaviour bug that happens
        # to contain 'message'.

        # Unmistakably about the words on the page, whatever else it says.
        wording_strong = re.search(
            r"\b(typo|spelling|misspell\w*|mis-spell\w*|reword\w*|"
            r"rewrite the (text|wording)|wording|rename\w*|call it|"
            r"says?|saying)\b", request, re.I)

        # Unmistakably about something not behaving. The stems below have
        # no trailing \b - "not updat" has to match "not updating", and a
        # word boundary right after "t" can never match inside that word,
        # which is why "the count is not updating" used to miss entirely.
        behaviour = re.search(
            r"\b(bug|broken|glitch|crash\w*|hang\w*|frozen|freeze|stuck|"
            r"unresponsive|dead button|fails? to)\b|"
            r"does ?n[o']?t work|do(es)? nothing|doing nothing|"
            r"no(t|thing) happens?|"
            r"isn'?t updat|is not updat|not updat|"
            r"isn'?t refresh|not refresh|"
            r"does ?n[o']?t (work|send|save|open|show|load|respond|"
            r"update|refresh|appear)|"
            r"won'?t (work|send|save|open|show|load|update|refresh|appear)|"
            r"\bshould (update|refresh|show|save|clear|work)\b",
            request, re.I)

        quoted = re.search(r'["“‘\']([^"”’\']{4,})["”’\']', request)
        # Deliberately NOT "text" on its own - "make the text bigger" or
        # "the text should be darker" are about how it looks, not what it
        # says, and a bare "text" trigger routed both to the wrong file.
        wording_soft = re.search(
            r"\b(word|words|heading|headline|title|caption|label|"
            r"greeting|message|sentence|phrase)\b", request, re.I)

        # Structural: moving something, adding something, or changing the
        # steps of a flow. These are the requests that CANNOT be satisfied
        # by one file - "put Settings at the top" is markup and the code
        # that drives it; "add a confirmation step before it sends" is a
        # dialog and the flow around it. Rewriting one half alone produces
        # a page that parses and does not work, which is exactly what the
        # self-test now catches - but it is better not to attempt the
        # impossible in the first place.
        structural = re.search(
            r"\b(move|relocate|put (it|the|that)|reorder|re-?order|rearrange|"
            r"swap|add (a|an|another)|remove the|get rid of|"
            r"confirm\w*|are you sure|double-?check step|extra step|"
            r"new (tab|button|section|panel|screen|step|page)|"
            r"combine|merge|split|collapse|group)\b", request, re.I)

        if structural and not wording_strong:
            target = "structural"
        elif wording_strong:
            target = "index.html"
        elif behaviour:
            target = "app.js"
        elif quoted or wording_soft:
            target = "index.html"
        else:
            target = "styles.css"
        if target == "structural":
            return self._upgrade_structural(request)

        src = WEB_DIR / target
        current = src.read_text(encoding="utf-8")

        backup_dir = DATA / "upgrade_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (backup_dir / f"{stamp}-{target}").write_text(current, encoding="utf-8")

        js_rules = (
            "- This is a browser JavaScript file, not a code-editing session - "
            "there is no linter and no test run before it ships, so it must be "
            "correct on the first try.\n"
            "- Keep every function name, every element id string, and the "
            "overall structure. Change only what the request asks for.\n"
            "- Every multi-line template literal must use backticks (`), never "
            "a single- or double-quoted string - a plain string containing a "
            "real newline is a syntax error that breaks the entire file, "
            "silently, with no visible sign on the page itself.\n"
        ) if target == "app.js" else ""

        brief = (
            "You are editing the front-end of a personal job-search app called "
            "Muster, used by one person, Sandra.\n\n"
            f"Rewrite the file {target} to satisfy this request:\n{request}\n\n"
            "Rules:\n"
            "- Output ONLY the complete new file contents. No commentary, no "
            "markdown fences.\n"
            "- Keep every id, class name and data attribute that already exists, "
            "or the app stops working.\n"
            "- Keep it accessible and readable in both light and dark mode.\n"
            + js_rules +
            "- If the request is vague, make a tasteful choice rather than asking."
        )
        try:
            new = _chat_claude_cli(brief, [], current)
        except (RuntimeError, OSError) as e:
            return {"error": f"Could not reach the assistant: {e}"}

        new = new.strip()
        # A fenced block, if there is one, is the most reliable signal - use
        # its contents and discard anything outside it (narration before or
        # after the fence).
        fence = re.search(r"```[a-zA-Z]*\n(.*?)```", new, re.S)
        if fence:
            new = fence.group(1).strip()
        else:
            # No fence: the model sometimes narrates first ("I've added a
            # handler... Here's the complete file:") with no code marker at
            # all, which otherwise ships a JS syntax error hidden inside an
            # English sentence. Cut everything before the first line that
            # actually looks like the start of this file type.
            starts = {
                "app.js": r"^\s*(/\*|//|'use strict'|\"use strict\"|const\b|let\b|"
                          r"var\b|function\b|async function\b|\(function|\(async)",
                "styles.css": r"^\s*(/\*|:root|\.[\w-]+\s*\{|@media|\*\s*\{)",
                "index.html": r"^\s*(<!doctype|<html)",
            }
            m = re.search(starts.get(target, r"^"), new, re.I | re.M)
            if m and m.start() > 0:
                new = new[m.start():].strip()

        # Structural guard. A rewrite that quietly drops half the page would
        # otherwise sail through a keyword check, so compare against what is
        # there now: every id and every CSS variable must survive, and the file
        # must not shrink dramatically.
        def ids(txt):
            return set(re.findall(r'id="([\w-]+)"', txt))

        problems = []
        if len(new) < len(current) * 0.75:
            problems.append(f"it shrank from {len(current)} to {len(new)} characters")

        if target == "index.html":
            lost = ids(current) - ids(new)
            if lost:
                problems.append("it dropped " + ", ".join(sorted(lost)[:6]))
            for needed in ("<html", "</html>", "app.js", "styles.css"):
                if needed not in new.lower():
                    problems.append(f"no {needed}")

        elif target == "app.js":
            # A syntax check is the only guard that actually matters here - this
            # exact class of bug (a template literal saved as a plain quoted
            # string, collapsing its escaped newlines into real ones) has
            # silently broken every event handler on this page before, with
            # nothing visibly wrong until a button was clicked.
            import subprocess
            import tempfile as _tf
            node = shutil.which("node")
            if node:
                with _tf.NamedTemporaryFile("w", suffix=".js", delete=False,
                                            encoding="utf-8") as f:
                    f.write(new)
                    check_path = f.name
                try:
                    r = subprocess.run([node, "--check", check_path],
                                       capture_output=True, text=True, timeout=15)
                    if r.returncode != 0:
                        # node's stderr ends with a "Node.js vX.Y.Z" trailer -
                        # the real message is the line naming the error itself.
                        err_lines = r.stderr.strip().splitlines()
                        err_line = next((ln for ln in err_lines if "Error" in ln),
                                        err_lines[0] if err_lines else "unknown error")
                        problems.append("it does not parse as JavaScript: "
                                        + err_line.strip()[:160])
                finally:
                    try:
                        os.unlink(check_path)
                    except OSError:
                        pass
            else:
                # No node on PATH - fall back to the balance check that has
                # caught this exact bug before.
                if new.count("try") - new.count("catch") - new.count("finally") > 2:
                    problems.append("try/catch looks unbalanced")

            lost_fns = set(re.findall(r"function\s+(\w+)\s*\(", current)) - \
                       set(re.findall(r"function\s+(\w+)\s*\(", new))
            if lost_fns:
                problems.append("it dropped functions: " + ", ".join(sorted(lost_fns)[:5]))
            lost_ids = set(re.findall(r"""['"]#([\w-]+)['"]""", current)) - \
                       set(re.findall(r"""['"]#([\w-]+)['"]""", new))
            if lost_ids:
                problems.append("it stopped referencing: " + ", ".join(f"#{i}" for i in sorted(lost_ids)[:5]))

        else:
            lost_vars = set(re.findall(r"(--[\w-]+):", current)) - \
                        set(re.findall(r"(--[\w-]+):", new))
            if lost_vars:
                problems.append("it dropped colours " + ", ".join(sorted(lost_vars)[:5]))
            if new.count("{") < current.count("{") * 0.8:
                problems.append("it dropped style rules")

        if problems:
            return {"error": "I did not apply that - the rewrite came back damaged: "
                             + "; ".join(problems) +
                             ". Nothing was changed. Try asking again, or more "
                             "specifically."}

        src.write_text(new, encoding="utf-8")

        # Static checks cannot see a page that parses and still renders dead:
        # a handler bound to an element that no longer exists throws at load
        # and takes every button after it down, silently. So now the page is
        # actually opened and used before this change is allowed to stand.
        verdict = self._self_test()
        if not verdict["ok"]:
            src.write_text(current, encoding="utf-8")
            (backup_dir / f"{stamp}-{target}").unlink(missing_ok=True)
            log_event(f"Upgrade rolled back - it broke the page: {request[:50]}")
            return {"error": "I made that change, opened the page to check it, "
                             "and it came back broken - so I put it back the way "
                             "it was. Nothing is lost. What went wrong: "
                             + "; ".join(verdict["failures"][:3]),
                    "rolled_back": True, "failures": verdict["failures"]}

        log_event(f"Front-end upgraded: {request[:70]}")
        msg = f"Updated {target}. Reload the page to see it."
        if verdict.get("skipped"):
            msg += " (I could not open a browser to test it, so give it a look.)"
        else:
            msg += f" I opened the page and checked it - {len(verdict['checks'])} checks passed."
        return {"ok": True, "file": target, "bytes": len(new),
                "backup": f"{stamp}-{target}", "tested": not verdict.get("skipped"),
                "checks": verdict.get("checks", []), "message": msg}

    def _run_chat_action(self, reply: str) -> str:
        """
        Carry out whatever the assistant said it was going to do.

        The app is a front end to Claude, so the conversation has to be able
        to ACT - otherwise every answer ends in "now go to the Jobs tab and
        press Find jobs", which is the thing this is supposed to spare her.
        The assistant appends one JSON object saying what it wants done; this
        runs it and replaces the JSON with plain English about what happened.

        Only the actions below are possible. Anything else in that JSON is
        ignored rather than guessed at, and nothing here submits an
        application - filling stops before Send, always, so the last word on
        anything going to an employer is hers.
        """
        # Brace-matched, not regex-matched. Several actions carry a nested
        # object ("fields", "places"), and a regex for a flat {...} matches
        # the INNER one - so the action was never seen, and the raw JSON was
        # left sitting in the reply for Sandra to read. Found exactly that
        # way: the details saved, and the JSON showed up on screen anyway.
        block = span = None
        for start in (i for i, c in enumerate(reply) if c == "{"):
            depth = 0
            for end in range(start, len(reply)):
                if reply[end] == "{":
                    depth += 1
                elif reply[end] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            cand = json.loads(reply[start:end + 1])
                        except ValueError:
                            break
                        if isinstance(cand, dict) and ("action" in cand or "upgrade" in cand):
                            block, span = cand, (start, end + 1)
                        break
        if block is None:
            return reply

        spoken = reply[:span[0]].strip()
        # the older single-key form, kept working
        if "upgrade" in block and "action" not in block:
            block = {"action": "upgrade", "request": block["upgrade"]}

        action = str(block.get("action", "")).lower()
        try:
            outcome = self._do_chat_action(action, block)
        except Exception as e:
            traceback.print_exc()
            outcome = (f"I tried to do that and it failed ({type(e).__name__}). "
                       "Nothing was changed - worth trying again.")

        return (spoken + "\n\n" + outcome).strip() if spoken else outcome

    def _do_chat_action(self, action: str, block: dict) -> str:
        if action == "scan":
            r = self._scan()
            return (f"Checked {r.get('checked', 0)} places and found "
                    f"{r.get('found', 0)} new. They are in the Jobs tab.")

        if action == "apply":
            url = str(block.get("url", "")).strip()
            if not url.startswith("http"):
                return ("I do not have the real address for that posting, so I "
                        "have not opened anything. Paste me the link and I will "
                        "fill it in.")
            rep = self._apply_start(url)
            if not rep.get("ok"):
                return f"That did not work: {rep.get('error', 'unknown problem')}"
            filled = len(rep.get("filled", []))
            note = " ".join(rep.get("notes", []))
            out = (f"Filled in {filled} field{'' if filled == 1 else 's'} and left "
                   "the browser open for you - read it over and press submit "
                   "yourself. I never send one on your behalf.")
            return out + (f"\n\nOne thing: {note}" if note else "")

        if action == "remember":
            fields = block.get("fields") or {}
            valid = {k for k, _ in REQUIRED_FIELDS}
            saved = []
            for k, v in fields.items():
                if k in valid and str(v).strip():
                    db().execute(
                        "INSERT INTO profile (key,value) VALUES (?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (k, str(v).strip()))
                    saved.append(k.replace("_", " "))
            if not saved:
                return "I did not catch anything I could record there."
            db().commit()
            log_event(f"Profile from chat: {', '.join(saved)}")
            return "Noted: " + ", ".join(saved) + "."

        if action == "watch":
            places = block.get("places") or []
            added = 0
            for pl in places:
                url = str(pl.get("url", "")).strip()
                if not url.startswith("http"):
                    continue
                db().execute(
                    "INSERT OR IGNORE INTO sources (name,url,kind) VALUES (?,?,?)",
                    (str(pl.get("name", url))[:120], url,
                     str(pl.get("kind", "general"))[:20]))
                added += 1
            if not added:
                return "I could not add that as a place to look."
            db().commit()
            log_event(f"Added {added} place(s) to look, from chat")
            return (f"Added {added} place{'' if added == 1 else 's'} to look. "
                    "I will check there from now on.")

        if action == "cert":
            name = str(block.get("name", "")).strip()
            if not name:
                return "I did not catch which certification you meant."
            db().execute(
                "INSERT INTO certs (name,status,expiry,source) VALUES (?,?,?,'chat')",
                (name[:120], str(block.get("status", "Complete"))[:30],
                 str(block.get("expiry", "") or "")[:20]))
            db().commit()
            log_event(f"Certification recorded: {name}")
            return f"Recorded {name}. It counts toward your match score now."

        if action == "schedule":
            try:
                hrs = int(block.get("hours", 0) or 0)
            except (TypeError, ValueError):
                return "I did not catch how often you wanted that."
            db().execute("INSERT INTO profile (key,value) VALUES "
                         "('scan_every_hours',?) ON CONFLICT(key) DO UPDATE SET "
                         "value=excluded.value", (str(hrs),))
            db().commit()
            log_event(f"Automatic job search set to every {hrs}h" if hrs
                      else "Automatic job search turned off")
            return (f"I will check for new postings every {hrs} hours."
                    if hrs else "Turned the automatic search off.")

        if action == "upgrade":
            res = self._upgrade(str(block.get("request", "")))
            return res.get("message") or res.get("error", "")

        return ""

    def _upgrade_structural(self, request: str) -> dict:
        """
        Change the shape of the app, not just its skin.

        Moving a control, adding a step to a flow, splitting a section -
        none of these live in one file. The markup and the code that drives
        it have to move together, so this rewrites both in one shot and
        treats the pair as a single change: both are validated, both are
        kept, or both are put back.
        """
        files = ["index.html", "app.js", "styles.css"]
        before = {f: (WEB_DIR / f).read_text(encoding="utf-8") for f in files}

        brief = (
            "You are restructuring the front-end of Muster, a personal "
            "job-search app used by one person, Sandra.\n\n"
            f"What she asked for:\n{request}\n\n"
            "You may change index.html, app.js and styles.css together. "
            "Output ONLY the files you actually need to change, each one "
            "complete, in exactly this format and nothing else:\n\n"
            "=== index.html ===\n<the whole file>\n=== app.js ===\n<the whole file>\n\n"
            "Hard rules:\n"
            "- Everything that works today must still work. This is a change "
            "on top of a working app, not a rewrite of it.\n"
            "- Keep every existing element id and every function name unless "
            "the request is explicitly to remove that thing. Markup and code "
            "are wired together by those ids; a rename in one file without "
            "the other gives a page that loads and does nothing.\n"
            "- If you add a control, wire it up in the same change.\n"
            "- No frameworks, no new files, no external requests.\n\n"
            "--- current index.html ---\n" + before["index.html"] +
            "\n--- current app.js ---\n" + before["app.js"]
        )

        try:
            raw = _chat_claude_cli(brief, [], request)
        except (RuntimeError, OSError) as e:
            return {"error": f"Could not reach the assistant: {e}"}

        # Pull the files back out of the reply.
        parts = re.split(r"^===\s*([\w.]+)\s*===\s*$", raw, flags=re.M)
        proposed: dict[str, str] = {}
        for i in range(1, len(parts) - 1, 2):
            name = parts[i].strip()
            body = parts[i + 1].strip()
            fence = re.search(r"```[a-zA-Z]*\n(.*?)```", body, re.S)
            if fence:
                body = fence.group(1).strip()
            if name in files and body:
                proposed[name] = body

        if not proposed:
            return {"error": "I could not make that change cleanly - the "
                             "assistant did not return the files in a usable "
                             "form. Nothing was changed."}

        problems = self._structural_problems(proposed, before)
        if problems:
            return {"error": "I did not apply that - the rewrite came back "
                             "damaged: " + "; ".join(problems[:3]) +
                             ". Nothing was changed."}

        backup_dir = DATA / "upgrade_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for name in proposed:
            (backup_dir / f"{stamp}-{name}").write_text(before[name], encoding="utf-8")
            (WEB_DIR / name).write_text(proposed[name], encoding="utf-8")

        verdict = self._self_test()
        if not verdict["ok"]:
            for name in proposed:
                (WEB_DIR / name).write_text(before[name], encoding="utf-8")
                (backup_dir / f"{stamp}-{name}").unlink(missing_ok=True)
            log_event(f"Structural upgrade rolled back - broke the page: {request[:40]}")
            return {"error": "I made that change, opened the page to check it, "
                             "and it came back broken - so I put everything back. "
                             "Nothing is lost. What went wrong: "
                             + "; ".join(verdict["failures"][:3]),
                    "rolled_back": True, "failures": verdict["failures"]}

        changed = ", ".join(sorted(proposed))
        log_event(f"Structural change: {request[:60]}")
        msg = f"Done - changed {changed}. Reload the page to see it."
        if verdict.get("skipped"):
            msg += " (I could not open a browser to test it, so give it a look.)"
        else:
            msg += f" I opened the page and checked it - {len(verdict['checks'])} checks passed."
        return {"ok": True, "file": changed, "structural": True,
                "tested": not verdict.get("skipped"),
                "checks": verdict.get("checks", []), "message": msg}

    def _structural_problems(self, proposed: dict, before: dict) -> list:
        """Static checks across a multi-file change, before anything is written."""
        problems = []
        for name, text in proposed.items():
            old = before[name]
            if len(text) < len(old) * 0.6:
                problems.append(f"{name} shrank from {len(old)} to {len(text)} characters")

            if name == "app.js":
                node = shutil.which("node")
                if node:
                    import subprocess
                    import tempfile as _tf
                    with _tf.NamedTemporaryFile("w", suffix=".js", delete=False,
                                                encoding="utf-8") as f:
                        f.write(text)
                        path = f.name
                    try:
                        r = subprocess.run([node, "--check", path],
                                           capture_output=True, text=True, timeout=15)
                        if r.returncode != 0:
                            lines = r.stderr.strip().splitlines()
                            err = next((l for l in lines if "Error" in l),
                                       lines[0] if lines else "unknown")
                            problems.append(f"app.js does not parse: {err.strip()[:120]}")
                    finally:
                        try:
                            os.unlink(path)
                        except OSError:
                            pass
                lost = set(re.findall(r"function\s+(\w+)\s*\(", old)) - \
                       set(re.findall(r"function\s+(\w+)\s*\(", text))
                if lost:
                    problems.append("app.js dropped functions: " + ", ".join(sorted(lost)[:5]))

            elif name == "index.html":
                for needed in ("<html", "</html>", "app.js", "styles.css"):
                    if needed not in text.lower():
                        problems.append(f"index.html no longer has {needed}")

            elif name == "styles.css":
                lost = set(re.findall(r"(--[\w-]+):", old)) - \
                       set(re.findall(r"(--[\w-]+):", text))
                if lost:
                    problems.append("styles.css dropped colours " + ", ".join(sorted(lost)[:5]))

        # ids are the contract BETWEEN the two files, so check them together
        html = proposed.get("index.html", before["index.html"])
        js = proposed.get("app.js", before["app.js"])
        html_ids = set(re.findall(r'id="([\w-]+)"', html))
        js_ids = set(re.findall(r"""['"]#([\w-]+)['"]""", js))
        orphaned = js_ids - html_ids
        was_orphaned = (set(re.findall(r"""['"]#([\w-]+)['"]""", before["app.js"]))
                        - set(re.findall(r'id="([\w-]+)"', before["index.html"])))
        newly_orphaned = orphaned - was_orphaned
        if newly_orphaned:
            problems.append(
                "the code would look for elements that are not in the page: "
                + ", ".join("#" + i for i in sorted(newly_orphaned)[:5]))
        return problems

    def _self_test(self) -> dict:
        """Open the real page and make sure it still works."""
        try:
            import smoke
            return smoke.run(f"http://127.0.0.1:{PORT}")
        except Exception as e:
            # The tester failing is not evidence the page is broken, and
            # refusing a good change because the harness misfired would be
            # its own kind of damage.
            return {"ok": True, "skipped": True, "checks": [], "failures": [],
                    "note": f"self-test unavailable ({e})"}

    def _upgrade_undo(self) -> dict:
        backup_dir = DATA / "upgrade_backups"
        backups = sorted(backup_dir.glob("*"), reverse=True) if backup_dir.exists() else []
        if not backups:
            return {"error": "Nothing to undo."}
        latest = backups[0]
        target = latest.name.split("-", 2)[-1]
        (WEB_DIR / target).write_text(latest.read_text(encoding="utf-8"), encoding="utf-8")
        latest.unlink()
        log_event(f"Undid the last upgrade to {target}")
        return {"ok": True, "message": f"Reverted {target}. Reload the page."}

    def _upgrade_reset(self) -> dict:
        """
        Put the design back exactly as it shipped.

        Undo walks back one change at a time and depends on the backup
        history being intact. This does not: docs/_defaults holds the
        original three files, so however far the app has drifted - a dozen
        upgrades deep, or one bad change with the backups cleared - there is
        always a known-good floor to fall back to. That floor is why
        experimenting with the look is safe.
        """
        src_dir = WEB_DIR / "_defaults"
        files = ["index.html", "app.js", "styles.css"]
        if not src_dir.exists() or not all((src_dir / f).exists() for f in files):
            return {"error": "The original design is not on disk to restore from."}

        backup_dir = DATA / "upgrade_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

        restored = []
        for f in files:
            live, original = WEB_DIR / f, src_dir / f
            if live.exists():
                # keep what she had, in case "restore" was the mistake
                (backup_dir / f"{stamp}-{f}").write_text(
                    live.read_text(encoding="utf-8"), encoding="utf-8")
            text = original.read_text(encoding="utf-8")
            if live.exists() and live.read_text(encoding="utf-8") == text:
                continue
            live.write_text(text, encoding="utf-8")
            restored.append(f)

        if not restored:
            return {"ok": True, "restored": [],
                    "message": "The design is already the original one."}

        log_event(f"Restored the original design ({', '.join(restored)})")
        return {"ok": True, "restored": restored,
                "message": "Put the original design back. Reload the page."}

    def _chat(self, message: str, history: list, context: str = "general") -> str:
        db().execute("INSERT INTO chat (role,text) VALUES ('user',?)", (message,))
        db().commit()

        prof = {r["key"]: r["value"] for r in db().execute("SELECT key,value FROM profile")}
        certs = [f"{r['name']} ({r['status']})" for r in
                 db().execute("SELECT name,status FROM certs")]
        open_jobs = [f"{r['employer']}: {r['title']}" for r in db().execute("""
            SELECT p.title, e.name AS employer FROM postings p
            LEFT JOIN employers e ON e.id=p.employer_id
            WHERE p.active=1 ORDER BY p.match DESC LIMIT 12""")]

        system = (
            "You are Muster, a calm and practical assistant helping Sandra Ayany "
            "find work in Alberta, Canada. Be concrete and encouraging without "
            "being saccharine. Never invent a posting, a deadline, or a "
            "requirement - if you do not know, say so and say how to find out.\n\n"

            "She has two tracks and may take work in either, or in anything else:\n\n"

            "FIRE SERVICE. She is a serving paid-on-call firefighter with Lac Ste. "
            "Anne County. Her NFPA 1001 is complete except live-fire evolutions, "
            "which is what 'seals' the certificate through IFSAC or Pro Board. This "
            "matters less than people assume: most paid-on-call departments do not "
            "require NFPA at all and train recruits themselves. The real gate on "
            "paid-on-call roles is usually the residency radius around a hall, not "
            "the certificate. Her own county provides Firefighter I and II free to "
            "members - contact R. Schroeder, rschroeder@lsac.ca, 780-785-3411. "
            "Alberta's certifying body is Municipal Affairs: ma.certexam@gov.ab.ca, "
            "1-866-421-6929.\n\n"

            "HEALTHCARE. She has nursing education but is not registered in Canada. "
            "Two separate conversations live here and you should not blur them:\n"
            "  (a) Work open to her TODAY without registration - Health Care Aide, "
            "continuing care assistant, home care, personal care attendant, unit "
            "clerk, medical office assistant, care aide with private operators and "
            "staffing agencies. Unregistered nursing education is a genuine asset "
            "for these, not a deficiency.\n"
            "  (b) The route THROUGH registration - in Alberta, CRNA for registered "
            "nurses and CLPNA for licensed practical nurses. Internationally trained "
            "applicants normally go through NNAS for credential assessment, and may "
            "need an English test such as CELBAN or IELTS. Do not guess at her "
            "specific pathway; ask where and when she trained, and what happened "
            "with registration, then reason from that.\n\n"

            "THE TWO TRACKS MEET. Edmonton Fire Rescue requires ONE qualifying "
            "medical credential at application, and its published list includes "
            "nursing registration (BN with CRNA, or LPN with CLPNA) alongside EMR "
            "and paramedic registration. So nursing registration would itself open "
            "the Edmonton firefighter route. Several other departments - St. Albert, "
            "Spruce Grove, Strathcona - require paramedic registration for their "
            "full-time roles, so a medical credential is the common key to career "
            "fire jobs. Raise this when it is relevant, but do not lecture about it "
            "every message.\n\n"

            "YOU CAN ACTUALLY DO THINGS, NOT JUST TALK ABOUT THEM. This app is "
            "a front end to you. Sandra should never have to go and find the "
            "right tab and press the right button - if she asks for something "
            "on this list, DO IT rather than telling her how. Reply with one "
            "short sentence saying what you are doing, then on a NEW LINE a "
            "single JSON object and nothing after it:\n"
            '{"action": "scan"}                     - look for new postings now\n'
            '{"action": "apply", "url": "https://..."} - open a posting and fill '
            "the application in for her. It STOPS before submitting; she reads "
            "it over and sends it herself.\n"
            '{"action": "remember", "fields": {"city": "Onoway"}} - record '
            "details about her. The field name must be one of exactly these, "
            "or it is silently dropped: "
            + ", ".join(k for k, _ in REQUIRED_FIELDS) + ".\n"
            '{"action": "watch", "places": [{"name": "Job Bank - nursing, Alberta", '
            '"url": "https://...", "kind": "healthcare"}]} - add a place to look. '
            "kind is fire, healthcare or general.\n"
            '{"action": "cert", "name": "NFPA 1001 Level I", "status": "Complete"} '
            "- record a certification. status is Complete, In progress, Partial "
            "or Expired.\n"
            '{"action": "schedule", "hours": 12} - check for jobs automatically '
            "every N hours. 0 turns it off.\n"
            '{"action": "upgrade", "request": "restated clearly"} - change how '
            "this app looks, reads or is laid out. Appearance and wording only, "
            "never job data.\n\n"
            "Only act when she is actually asking for it. If she is thinking out "
            "loud, or asking what you think, just answer - do not fire an action "
            "at her. Never invent a URL for an application: if you do not have "
            "the real address, say so and ask."

            "Applications close on fixed dates and Alberta municipal hiring clusters "
            "in the fall, so timing matters more than volume. If she asks what to do "
            "next, prefer the specific and immediate over the general.\n\n"

            f"Her profile: {json.dumps(prof, default=str)[:1800]}\n"
            f"Her certifications: {', '.join(certs) or 'none recorded yet'}\n"
            f"Currently tracked openings: {'; '.join(open_jobs) or 'none scanned yet'}"
        )

        FOCUS = {
            "jobs": ("The user is looking at the JOBS tab. Talk about employers, "
                     "postings, deadlines and whether something is worth applying "
                     "to. Be blunt about odds and about residency rules, which "
                     "disqualify more paid-on-call applicants than certificates do."),
            "documents": ("The user is looking at the DOCUMENTS tab. Talk about "
                          "resumes, certificates, expiry dates, driver abstracts, "
                          "record checks and what a given employer will want "
                          "attached. Tell her what is missing."),
            "profile": ("The user is looking at the PROFILE form and may be asking "
                        "what to put in a field. Answer briefly and concretely, and "
                        "say what employers actually do with that answer."),
        }
        if context in FOCUS:
            system += "\n\n" + FOCUS[context]

        provider = ENV.get("CHAT_PROVIDER", "ollama").lower()
        try:
            fn = {"anthropic": _chat_anthropic,
                  "claude-cli": _chat_claude_cli}.get(provider, _chat_ollama)
            reply = fn(system, history, message)
        except Exception as e:
            reply = (f"I could not reach the {provider} backend ({e}).\n\n"
                     "If you are using Ollama, check it is running:  ollama serve\n"
                     "Or set CHAT_PROVIDER=anthropic with an API key in .env.")

        reply = self._run_chat_action(reply)

        db().execute("INSERT INTO chat (role,text) VALUES ('assistant',?)", (reply,))
        db().commit()
        return reply


def _chat_ollama(system: str, history: list, message: str) -> str:
    msgs = [{"role": "system", "content": system}]
    for h in history[-12:]:
        msgs.append({"role": "user" if h.get("role") == "user" else "assistant",
                     "content": h.get("text", "")})
    msgs.append({"role": "user", "content": message})
    # num_gpu 0 keeps inference on the CPU so the GPU stays free for other work.
    # Set OLLAMA_NUM_GPU to a positive number in .env to use the graphics card.
    opts = {"num_gpu": int(ENV.get("OLLAMA_NUM_GPU", 0))}
    payload = json.dumps({
        "model": ENV.get("OLLAMA_MODEL", "qwen2.5-coder:7b"),
        "messages": msgs, "stream": False, "options": opts}).encode()
    host = ENV.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    req = urllib.request.Request(f"{host}/api/chat", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["message"]["content"]


def _chat_claude_cli(system: str, history: list, message: str) -> str:
    """
    Use the Claude Code CLI already installed and signed in on this machine.
    No API key needed - it rides the existing subscription.
    """
    import subprocess
    nl = chr(10)
    convo = nl.join(
        f"{'Sandra' if h.get('role') == 'user' else 'You'}: {h.get('text','')}"
        for h in history[-10:])
    # The briefing goes in as a SYSTEM prompt, not mixed into the user turn -
    # otherwise the model reads it as instructions about a third party and
    # greets you instead of answering the question.
    sys_prompt = (
        system + nl + nl +
        "You are talking directly to Sandra herself. Address her as 'you'. "
        "Answer the question she actually asked, immediately, in plain prose. "
        "No greeting, no menu of options, no offer to help - just the answer."
    )
    # Label the transcript unmistakably and put the live question last, or the
    # model reads the pasted history as ambient "session context" and asks what
    # you want instead of answering.
    if convo:
        user = (f"[Transcript of our earlier messages - context only, do not"
                f" reply to these]{nl}{convo}{nl}"
                f"[End of transcript]{nl}{nl}"
                f"Sandra's new question, answer this one:{nl}{message}")
    else:
        user = message
    # Three things matter here, each of which broke it in turn:
    #   1. --system-prompt REPLACES Claude Code's coding-agent prompt.
    #      --append- leaves it a coding assistant that talks about the repo.
    #   2. Never shell=True with an argument list on Windows - cmd.exe mangles
    #      a long multi-line prompt and the message never arrives. Resolve the
    #      real executable and pass argv directly.
    #   3. Run from a neutral directory so it does not pick up this project's
    #      files, CLAUDE.md or git status as "session context".
    import shutil
    import tempfile

    exe = ENV.get("CLAUDE_CLI") or shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI not found on PATH")

    # Outside the repo entirely - a directory inside the working tree still
    # shows up in git status and leaks the project as context.
    neutral = Path(tempfile.gettempdir()) / "muster_chat"
    neutral.mkdir(parents=True, exist_ok=True)

    # System prompt via file - avoids Windows command-line length limits.
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8", dir=str(neutral)) as f:
        f.write(sys_prompt)
        sys_file = f.name

    try:
        # The message goes in on STDIN, not argv. A multi-line prompt passed
        # as an argument gets mangled and the model never sees the question.
        proc = subprocess.run(
            [exe, "-p", "--system-prompt-file", sys_file,
             "--output-format", "text"],
            input=user, capture_output=True, text=True, timeout=240,
            cwd=str(neutral), encoding="utf-8", errors="replace")
    finally:
        try:
            os.unlink(sys_file)
        except OSError:
            pass
    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError((proc.stderr or "claude cli returned nothing")[:200])
    return out


def _chat_anthropic(system: str, history: list, message: str) -> str:
    key = ENV.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in .env")
    msgs = []
    for h in history[-12:]:
        msgs.append({"role": "user" if h.get("role") == "user" else "assistant",
                     "content": h.get("text", "")})
    msgs.append({"role": "user", "content": message})
    payload = json.dumps({"model": "claude-sonnet-5", "max_tokens": 1400,
                          "system": system, "messages": msgs}).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=payload,
        headers={"Content-Type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["content"][0]["text"]


# ─────────────────────────── main ──────────────────────────────
def lan_ip() -> str:
    """This machine's address on the home network, for other devices to use."""
    import socket
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.connect(("10.255.255.255", 1))   # no packet is sent; just picks the route
        ip = sk.getsockname()[0]
        sk.close()
        return ip
    except OSError:
        return ""


def main() -> None:
    db()
    if not db().execute("SELECT COUNT(*) c FROM employers").fetchone()["c"]:
        seed_employers()
    if not db().execute("SELECT COUNT(*) c FROM sources").fetchone()["c"]:
        seed_sources()

    tokset = TOKEN and TOKEN != "change-me-to-something-random"

    if BIND_HOST in ("0.0.0.0", "::"):
        ip = lan_ip()
        network_line = (f"http://{ip}:{PORT}  <- other devices on your home wifi"
                        if ip else "listening on every interface")
        pin_line = ("PIN set - a new device registers once, then is remembered"
                    if ACCESS_PIN else
                    "NO ACCESS_PIN SET - other devices will be refused")
    else:
        network_line = "this machine only (set BIND_HOST=0.0.0.0 in .env to share)"
        pin_line = "not needed - nothing but this machine can reach the engine"
    banner = f"""
  +----------------------------------------------+
  |   MUSTER  ::  local engine v{VERSION}            |
  +----------------------------------------------+

   API      http://127.0.0.1:{PORT}
   Network  {network_line}
   Data     {DATA}
   Auth     {"token required" if tokset else "OPEN - set API_TOKEN in .env"}
   Devices  {pin_line}
   Gmail    {"app password loaded" if ENV.get("GMAIL_APP_PASSWORD") else "not configured"}
   Chat     {ENV.get("CHAT_PROVIDER", "ollama")}

   Front-end: open docs/index.html, or the GitHub Pages site,
   then click Connect and paste the API token.

   Ctrl-C to stop.
"""
    print(banner)

    class MusterServer(ThreadingHTTPServer):
        # A per-request thread that never finishes - a socket left half-open
        # by a client that vanished mid-request - would otherwise sit there
        # forever with daemon_threads unset. daemon_threads=True means Python
        # can still exit cleanly regardless, and it costs nothing when every
        # request completes normally, which is the overwhelmingly common case.
        daemon_threads = True

    Handler.timeout = 120   # a stalled client socket gets reclaimed, not held forever
    MusterServer((BIND_HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  stopped.\n")
