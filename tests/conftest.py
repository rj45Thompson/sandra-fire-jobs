"""
Shared fixtures.

Every test runs against a throwaway SQLite file, never Sandra's real
data/muster.db. server.py resolves DB_PATH once at import and caches one
connection per thread on a threading.local, so both have to be redirected
together or the tests quietly read the real database instead.
"""

import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import server  # noqa: E402


def make_docx(path: Path, body: str) -> Path:
    """A real .docx - a zip with word/document.xml - not a text file renamed."""
    paras = "".join(
        f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in body.strip().split("\n")
    )
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paras}</w:body></w:document>"
    )
    types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("word/document.xml", doc)
    return path


RESUME_BODY = """Sandra Thompson
Onoway, Alberta  T0E 1V0
sandra.test@example.com
780-555-0142

Paid-on-call firefighter with Lac Ste. Anne County.
NFPA 1001 Level I complete. Standard First Aid and CPR-C current.
Class 5 driver's licence.
"""


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point server.py at an empty database for the duration of one test."""
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(server, "DATA", tmp_path)
    if hasattr(server._local, "conn"):
        del server._local.conn
    conn = server.db()
    yield conn
    conn.close()
    if hasattr(server._local, "conn"):
        del server._local.conn


@pytest.fixture
def resume(tmp_path):
    return make_docx(tmp_path / "resume.docx", RESUME_BODY)
