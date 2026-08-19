"""
Résumé text extraction.

This is the code that turned out to be able to take the whole server down:
a .docx that is not a valid zip raised BadZipFile, which is not an OSError,
KeyError or ValueError, so it escaped the original except clause mid-request
- after the file was already written and its row already committed, but
before the response was sent. The upload looked like it failed, the file sat
marked "local only" forever, and nothing was logged. Hence the corrupt-input
cases below: they are the regression, not padding.
"""

from pathlib import Path

from conftest import make_docx

import server


def test_reads_text_from_a_real_docx(tmp_path):
    path = make_docx(tmp_path / "r.docx", "Sandra Thompson\nOnoway, Alberta")
    text = server.resume_text(path)
    assert "Sandra Thompson" in text
    assert "Onoway" in text


def test_reads_a_plain_text_resume(tmp_path):
    path = tmp_path / "r.txt"
    path.write_text("Sandra Thompson\nfirefighter", encoding="utf-8")
    assert "firefighter" in server.resume_text(path)


def test_corrupt_docx_returns_empty_instead_of_raising(tmp_path):
    """A .docx that is not a zip at all - the exact shape that crashed it."""
    path = tmp_path / "fake.docx"
    path.write_bytes(b"this is not a zip file, it is just some bytes")
    assert server.resume_text(path) == ""


def test_docx_zip_without_document_xml_returns_empty(tmp_path):
    """A valid zip missing the part we read raises KeyError deeper in."""
    import zipfile

    path = tmp_path / "empty.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("something/else.xml", "<x/>")
    assert server.resume_text(path) == ""


def test_docx_with_malformed_xml_returns_empty(tmp_path):
    import zipfile

    path = tmp_path / "bad.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", "<w:document><unclosed>")
    # never raises; may return partial text or nothing, but must not blow up
    assert isinstance(server.resume_text(path), str)


def test_unreadable_path_returns_empty(tmp_path):
    assert server.resume_text(tmp_path / "does_not_exist.docx") == ""


def test_unknown_extension_returns_empty(tmp_path):
    path = tmp_path / "resume.xyz"
    path.write_text("Sandra Thompson", encoding="utf-8")
    assert server.resume_text(path) == ""
