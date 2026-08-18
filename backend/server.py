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
import json
import os
import re
import sqlite3
import sys
import threading
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

CREATE TABLE IF NOT EXISTS certs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, status TEXT, expiry TEXT,
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

CREATE TABLE IF NOT EXISTS chat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT, text TEXT, at TEXT DEFAULT CURRENT_TIMESTAMP);
"""

_local = threading.local()


def db() -> sqlite3.Connection:
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.executescript(SCHEMA)
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

    def _authed(self) -> bool:
        if not TOKEN:
            return True   # no token configured = local dev, allow
        return self.headers.get("X-Muster-Token") == TOKEN

    def log_message(self, fmt, *args):
        print(f"  {self.command} {self.path}")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors(self.headers.get("Origin"))
        self.end_headers()

    # -- routes --
    def do_GET(self):
        p = self.path.split("?")[0]
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

        if p == "/stats":
            return self._send(self._stats())

        return self._send({"error": "not found"}, 404)

    def do_POST(self):
        p = self.path.split("?")[0]
        if not self._authed():
            return self._send({"error": "bad token"}, 401)
        b = self._body()

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
            return self._send({"ok": True, "path": str(target)})

        if p == "/employers/seed":
            return self._send({"ok": True, "count": seed_employers()})

        if p == "/scan":
            return self._send(self._scan())

        if p == "/chat":
            return self._send({"reply": self._chat(b.get("message", ""),
                                                   b.get("history", []))})

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
        for e in db().execute("SELECT * FROM employers").fetchall():
            url = e["careers_url"]
            if not url:
                continue
            checked += 1
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0 (Muster job watcher)"})
                with urllib.request.urlopen(req, timeout=12) as resp:
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
        return {"ok": True, "checked": checked, "new": found, "skipped_by_robots": skipped}

    def _chat(self, message: str, history: list) -> str:
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

            "Applications close on fixed dates and Alberta municipal hiring clusters "
            "in the fall, so timing matters more than volume. If she asks what to do "
            "next, prefer the specific and immediate over the general.\n\n"

            f"Her profile: {json.dumps(prof, default=str)[:1800]}\n"
            f"Her certifications: {', '.join(certs) or 'none recorded yet'}\n"
            f"Currently tracked openings: {'; '.join(open_jobs) or 'none scanned yet'}"
        )

        provider = ENV.get("CHAT_PROVIDER", "ollama").lower()
        try:
            reply = (_chat_anthropic if provider == "anthropic" else _chat_ollama)(
                system, history, message)
        except Exception as e:
            reply = (f"I could not reach the {provider} backend ({e}).\n\n"
                     "If you are using Ollama, check it is running:  ollama serve\n"
                     "Or set CHAT_PROVIDER=anthropic with an API key in .env.")

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
def main() -> None:
    db()
    if not db().execute("SELECT COUNT(*) c FROM employers").fetchone()["c"]:
        seed_employers()

    tokset = TOKEN and TOKEN != "change-me-to-something-random"
    banner = f"""
  +----------------------------------------------+
  |   MUSTER  ::  local engine v{VERSION}            |
  +----------------------------------------------+

   API      http://127.0.0.1:{PORT}
   Data     {DATA}
   Auth     {"token required" if tokset else "OPEN - set API_TOKEN in .env"}
   Gmail    {"app password loaded" if ENV.get("GMAIL_APP_PASSWORD") else "not configured"}
   Chat     {ENV.get("CHAT_PROVIDER", "ollama")}

   Front-end: open docs/index.html, or the GitHub Pages site,
   then click Connect and paste the API token.

   Ctrl-C to stop.
"""
    print(banner)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  stopped.\n")
