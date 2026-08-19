"""
Uploading a résumé fills in the "Still needed" list.

This is the behaviour behind the merged Profile & documents section: drop
one file in and the questions an application will ask answer themselves.
"""

from conftest import make_docx

import server


def test_lifts_the_obvious_details_off_a_resume(db, resume):
    n = server.profile_from_resume(resume)
    assert n > 0

    prof = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM profile")}
    assert prof["first_name"] == "Sandra"
    assert prof["last_name"] == "Thompson"
    assert prof["email"] == "sandra.test@example.com"
    assert prof["phone"] == "780-555-0142"
    assert prof["city"] == "Onoway"
    assert prof["province"] == "Alberta"
    assert prof["postal"] == "T0E 1V0"


def test_never_overwrites_something_she_told_us_herself(db, resume):
    """
    An answer she typed in chat outranks anything read off a file. If a
    résumé is stale - an old phone number, a previous town - re-uploading
    it must not silently undo a correction she made by hand.
    """
    db.execute("INSERT INTO profile (key, value) VALUES ('phone', '780-555-9999')")
    db.commit()

    server.profile_from_resume(resume)

    phone = db.execute("SELECT value FROM profile WHERE key='phone'").fetchone()["value"]
    assert phone == "780-555-9999"


def test_counts_only_fields_it_actually_added(db, resume):
    first = server.profile_from_resume(resume)
    second = server.profile_from_resume(resume)
    assert first > 0
    # everything was already there the second time
    assert second == 0


def test_unparseable_resume_adds_nothing_and_does_not_raise(db, tmp_path):
    path = tmp_path / "broken.docx"
    path.write_bytes(b"not a zip")
    assert server.profile_from_resume(path) == 0
    assert db.execute("SELECT COUNT(*) c FROM profile").fetchone()["c"] == 0


def test_does_not_invent_fields_that_are_not_there(db, tmp_path):
    """A résumé with only a name must not conjure a phone or postal code."""
    path = make_docx(tmp_path / "sparse.docx", "Sandra Thompson\nFirefighter")
    server.profile_from_resume(path)

    prof = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM profile")}
    assert prof.get("first_name") == "Sandra"
    assert "phone" not in prof
    assert "postal" not in prof
    assert "email" not in prof


def test_certifications_come_off_the_resume(db, resume):
    n = server.lift_certs_from_resume(resume)
    assert n > 0
    names = [r["name"] for r in db.execute("SELECT name FROM certs")]
    joined = " ".join(names).lower()
    assert "nfpa" in joined or "first aid" in joined


def test_re_uploading_does_not_duplicate_certifications(db, resume):
    server.lift_certs_from_resume(resume)
    before = db.execute("SELECT COUNT(*) c FROM certs").fetchone()["c"]
    server.lift_certs_from_resume(resume)
    after = db.execute("SELECT COUNT(*) c FROM certs").fetchone()["c"]
    assert after == before


def test_gap_list_shrinks_by_exactly_what_was_filled(db, resume):
    total = len(server.REQUIRED_FIELDS)

    def gaps():
        prof = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM profile")}
        return [k for k, _ in server.REQUIRED_FIELDS if not str(prof.get(k, "")).strip()]

    assert len(gaps()) == total
    added = server.profile_from_resume(resume)
    assert len(gaps()) == total - added
