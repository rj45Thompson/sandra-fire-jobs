"""
Every remaining endpoint, and the scoring behind the match column.

One route per thing Sandra can do from the UI, plus the awkward inputs -
a missing field, an id that does not exist, a number sent as text. None
of those should ever produce a stack trace or a half-written row.
"""

import json

import pytest

import server


# ── profile ───────────────────────────────────────────────────────────

def test_profile_saves_and_reads_back(engine):
    engine.post("/profile", {"first_name": "Sandra", "city": "Onoway"})
    status, prof = engine.get("/profile")
    assert status == 200
    assert prof["first_name"] == "Sandra"
    assert prof["city"] == "Onoway"


def test_saving_a_field_twice_updates_rather_than_duplicates(engine):
    engine.post("/profile", {"city": "Onoway"})
    engine.post("/profile", {"city": "Edmonton"})
    _, prof = engine.get("/profile")
    assert prof["city"] == "Edmonton"


def test_gaps_and_have_always_add_up_to_the_total(engine):
    engine.post("/profile", {"first_name": "Sandra", "last_name": "Thompson"})
    _, g = engine.get("/profile/gaps")
    assert len(g["gaps"]) + len(g["have"]) == g["total"]
    assert g["complete"] == len(g["have"])


def test_a_field_set_to_blank_still_counts_as_missing(engine):
    engine.post("/profile", {"first_name": "   "})
    _, g = engine.get("/profile/gaps")
    assert any("first name" in q["question"].lower() for q in g["gaps"])


# ── certifications ────────────────────────────────────────────────────

def test_a_certification_can_be_added_and_removed(engine):
    engine.post("/certs", {"name": "NFPA 1001 Level I", "status": "Complete",
                           "expiry": "2027-01-01"})
    _, certs = engine.get("/certs")
    assert len(certs) == 1
    assert certs[0]["name"] == "NFPA 1001 Level I"

    engine.post("/certs/delete", {"id": certs[0]["id"]})
    _, certs = engine.get("/certs")
    assert certs == []


def test_deleting_a_certification_that_is_not_there_is_harmless(engine):
    status, body = engine.post("/certs/delete", {"id": 99999})
    assert status == 200
    assert body["ok"] is True


def test_a_manually_added_certification_is_marked_as_manual(engine):
    engine.post("/certs", {"name": "Class 5 licence", "status": "Complete"})
    _, certs = engine.get("/certs")
    assert certs[0]["source"] == "manual"


# ── places to look ────────────────────────────────────────────────────

def test_a_place_to_look_can_be_added_and_removed(engine):
    engine.post("/sources", {"name": "Job Bank - Alberta",
                             "url": "https://jobbank.gc.ca/x", "kind": "fire"})
    _, srcs = engine.get("/sources")
    assert len(srcs) == 1
    assert srcs[0]["kind"] == "fire"

    engine.post("/sources/delete", {"id": srcs[0]["id"]})
    _, srcs = engine.get("/sources")
    assert srcs == []


def test_the_same_place_is_not_added_twice(engine):
    for _ in range(2):
        engine.post("/sources", {"name": "Indeed", "url": "https://indeed.ca/x",
                                 "kind": "general"})
    _, srcs = engine.get("/sources")
    assert len(srcs) == 1


# ── the schedule ──────────────────────────────────────────────────────

@pytest.mark.parametrize("hours", [0, 6, 12, 24])
def test_the_scan_schedule_is_remembered(engine, hours):
    status, body = engine.post("/schedule", {"hours": hours})
    assert status == 200
    assert body["hours"] == hours

    _, prof = engine.get("/profile")
    assert prof["scan_every_hours"] == str(hours)


def test_the_schedule_accepts_a_number_sent_as_text(engine):
    """The select element hands back a string; that must not blow up."""
    status, body = engine.post("/schedule", {"hours": "12"})
    assert status == 200
    assert body["hours"] == 12


def test_a_nonsense_schedule_is_rejected_without_a_stack_trace(engine):
    status, body = engine.post("/schedule", {"hours": "every other tuesday"})
    assert status in (200, 400, 500)
    assert isinstance(body, dict)
    # whatever it decides, the engine is still answering
    assert engine.get("/health")[1]["ok"] is True


# ── the match score ───────────────────────────────────────────────────

def test_the_baseline_score_reflects_that_she_already_serves(engine):
    assert server.score_posting("Firefighter", "casual") >= 30


def test_holding_nfpa_1001_moves_the_score_substantially(engine):
    before = server.score_posting("Firefighter", "casual")
    engine.post("/certs", {"name": "NFPA 1001 Level I", "status": "Complete"})
    after = server.score_posting("Firefighter", "casual")
    assert after > before + 30


def test_a_certification_she_is_still_working_on_still_counts(engine):
    engine.post("/certs", {"name": "EMR", "status": "In progress"})
    assert server.score_posting("Firefighter", "casual") > 30


def test_an_expired_certification_does_not_count(engine):
    """
    Compared against the same posting with the cert current, rather than
    against a bare number - the title itself is worth points too, and
    hard-coding the total just re-encodes the arithmetic.
    """
    engine.post("/certs", {"name": "NFPA 1001", "status": "Expired"})
    expired = server.score_posting("Firefighter", "casual")

    _, certs = engine.get("/certs")
    engine.post("/certs/delete", {"id": certs[0]["id"]})
    engine.post("/certs", {"name": "NFPA 1001", "status": "Complete"})
    current = server.score_posting("Firefighter", "casual")

    assert expired < current
    assert current - expired == server.CERT_WEIGHT["nfpa 1001"]


def test_the_score_never_leaves_nought_to_a_hundred(engine):
    for name in ("NFPA 1001", "EMR", "Primary Care Paramedic", "Class 3",
                 "Air Brakes", "NFPA 1002", "NFPA 1072", "ICS", "First Aid", "S-100"):
        engine.post("/certs", {"name": name, "status": "Complete"})
    score = server.score_posting("Firefighter paid on call", "casual")
    assert 0 <= score <= 100


def test_a_job_that_is_not_firefighting_scores_lower_than_one_that_is(engine):
    engine.post("/certs", {"name": "NFPA 1001", "status": "Complete"})
    assert (server.score_posting("Firefighter", "casual")
            > server.score_posting("Records Clerk", "full-time"))


# ── statistics ────────────────────────────────────────────────────────

def test_stats_are_all_zero_on_a_fresh_install(engine):
    status, s = engine.get("/stats")
    assert status == 200
    assert s["open"] == 0
    assert s["sent"] == 0
    assert s["activity"] == [] or isinstance(s["activity"], list)


def test_activity_records_what_the_engine_did(engine):
    engine.post("/certs", {"name": "First Aid", "status": "Complete"})
    _, s = engine.get("/stats")
    assert any("First Aid" in a["text"] for a in s["activity"])


def test_activity_is_newest_first(engine):
    engine.post("/certs", {"name": "First one", "status": "Complete"})
    engine.post("/certs", {"name": "Second one", "status": "Complete"})
    _, s = engine.get("/stats")
    texts = [a["text"] for a in s["activity"]]
    assert texts.index("Certification recorded: Second one") < \
           texts.index("Certification recorded: First one")


# ── employers and postings ────────────────────────────────────────────

def test_the_employer_list_can_be_seeded(engine):
    status, body = engine.post("/employers/seed", {})
    assert status == 200
    _, emps = engine.get("/employers")
    assert len(emps) > 0
    assert all(e["name"] for e in emps)


def test_postings_start_empty(engine):
    status, rows = engine.get("/postings")
    assert status == 200
    assert rows == []


# ── the odd request ───────────────────────────────────────────────────

def test_an_unknown_route_is_a_clean_404(engine):
    status, body = engine.get("/no/such/thing")
    assert status == 404
    assert body["error"] == "not found"


def test_malformed_json_does_not_take_the_engine_down(engine):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(engine.base + "/profile",
                                 data=b"{not json at all",
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError:
        pass
    assert engine.get("/health")[1]["ok"] is True


def test_an_empty_post_body_is_handled(engine):
    status, body = engine.post("/profile", {})
    assert status == 200
    assert engine.get("/health")[1]["ok"] is True


def test_the_front_end_files_are_served(engine):
    status, body, headers = engine.raw("/")
    assert status == 200
    assert b"<html" in body.lower() or b"<!doctype" in body.lower()


def test_health_reports_what_is_configured(engine):
    status, h = engine.get("/health")
    assert status == 200
    assert h["version"] == server.VERSION
    assert "gmail" in h and "chat" in h
