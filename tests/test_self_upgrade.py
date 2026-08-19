"""
The app rewriting its own front-end.

Riskiest surface in the project: a bad rewrite here does not throw, it
ships. A JS file that does not parse silently kills every button on the
page with nothing visibly wrong until something is clicked, and that has
actually happened twice. So the guard matters more than the feature, and
most of this file is about what must be REFUSED.

The assistant is mocked throughout - each test decides what it "returns"
and then checks what the engine does with it.
"""

import pytest

import server


@pytest.fixture
def web(tmp_path, monkeypatch):
    """A throwaway copy of the three files the upgrader may touch."""
    d = tmp_path / "web"
    d.mkdir()
    (d / "index.html").write_text(
        '<!doctype html><html><head><link rel="stylesheet" href="styles.css">'
        '</head><body><div id="nav"></div><div id="gap-list"></div>'
        '<div id="cert-list"></div><script src="app.js"></script></body></html>',
        encoding="utf-8")
    (d / "app.js").write_text(
        "/* app */\nfunction loadApps() { return 1; }\n"
        "function loadGaps() { return 2; }\nconst x = $('#nav');\n" + "// pad\n" * 60,
        encoding="utf-8")
    (d / "styles.css").write_text(
        ":root{ --pink:#FF2E88; --cream:#FFF8F0; --ink:#2B1B2E; }\n"
        ".card{ padding:1rem; }\n.btn{ border-radius:99px; }\n" + ".x{color:red}\n" * 60,
        encoding="utf-8")
    monkeypatch.setattr(server, "WEB_DIR", d)
    return d


def replies(monkeypatch, text):
    monkeypatch.setattr(server, "_chat_claude_cli", lambda *a, **k: text)


# ── which file a request is routed to ─────────────────────────────────

@pytest.mark.parametrize("request_text,expected", [
    ("make the buttons more rounded",                 "styles.css"),
    ("change the background colour to teal",          "styles.css"),
    ("make the text bigger",                          "styles.css"),
    ('change the heading to say "Good morning"',      "index.html"),
    ("reword the welcome message",                    "index.html"),
    ("fix the typo in the title",                     "index.html"),
    ("the View button does nothing",                  "app.js"),
    ("the certification count is not updating",       "app.js"),
    ("bug: clicking save does not work",              "app.js"),
])
def test_a_request_reaches_the_right_file(engine, web, monkeypatch, request_text, expected):
    seen = {}

    def capture(system_or_brief, history, current):
        seen["brief"] = system_or_brief
        return (web / expected).read_text(encoding="utf-8")

    monkeypatch.setattr(server, "_chat_claude_cli", capture)
    status, body = engine.post("/upgrade", {"request": request_text})
    assert body.get("file") == expected, body


def test_a_bug_report_never_becomes_a_stylesheet_rewrite(engine, web, monkeypatch):
    """
    The failure RJ suspected: reporting a broken button used to be routed
    to CSS, where it could only ever produce nonsense.
    """
    monkeypatch.setattr(server, "_chat_claude_cli",
                        lambda *a, **k: (web / "app.js").read_text(encoding="utf-8"))
    _, body = engine.post("/upgrade",
                          {"request": "the View button on the applications tab does nothing"})
    assert body["file"] == "app.js"


# ── what must be refused ──────────────────────────────────────────────

def test_javascript_that_does_not_parse_is_refused(engine, web, monkeypatch):
    original = (web / "app.js").read_text(encoding="utf-8")
    replies(monkeypatch, original + "\nfunction broken( { syntax error here\n")

    _, body = engine.post("/upgrade", {"request": "fix the click handler bug"})
    assert "error" in body
    assert "does not parse" in body["error"]
    assert (web / "app.js").read_text(encoding="utf-8") == original, "file must be untouched"


def test_a_rewrite_that_drops_half_the_page_is_refused(engine, web, monkeypatch):
    original = (web / "styles.css").read_text(encoding="utf-8")
    replies(monkeypatch, ":root{ --pink:#000; }")

    _, body = engine.post("/upgrade", {"request": "make it darker"})
    assert "error" in body
    assert "shrank" in body["error"]
    assert (web / "styles.css").read_text(encoding="utf-8") == original


def test_a_stylesheet_that_loses_the_palette_is_refused(engine, web, monkeypatch):
    original = (web / "styles.css").read_text(encoding="utf-8")
    replies(monkeypatch, ".card{ padding:1rem; }\n" + ".x{color:red}\n" * 80)

    _, body = engine.post("/upgrade", {"request": "simplify the styles"})
    assert "error" in body
    assert "colours" in body["error"]
    assert (web / "styles.css").read_text(encoding="utf-8") == original


def test_html_that_drops_an_element_the_code_needs_is_refused(engine, web, monkeypatch):
    original = (web / "index.html").read_text(encoding="utf-8")
    replies(monkeypatch,
            '<!doctype html><html><head><link rel="stylesheet" href="styles.css">'
            '</head><body><div id="nav"></div>'
            '<script src="app.js"></script></body></html>' + "<!-- pad -->" * 200)

    _, body = engine.post("/upgrade", {"request": 'change the heading to say "Hello"'})
    assert "error" in body
    assert "dropped" in body["error"]
    assert (web / "index.html").read_text(encoding="utf-8") == original


def test_javascript_that_drops_a_function_is_refused(engine, web, monkeypatch):
    original = (web / "app.js").read_text(encoding="utf-8")
    replies(monkeypatch, "/* app */\nfunction loadApps() { return 1; }\n"
                         "const x = $('#nav');\n" + "// pad\n" * 60)

    _, body = engine.post("/upgrade", {"request": "fix the broken button"})
    assert "error" in body
    assert "dropped functions" in body["error"]
    assert (web / "app.js").read_text(encoding="utf-8") == original


def test_an_empty_request_asks_her_what_she_wants(engine, web):
    status, body = engine.post("/upgrade", {"request": "   "})
    assert "error" in body


# ── narration around the code ─────────────────────────────────────────

def test_a_fenced_code_block_is_unwrapped(engine, web, monkeypatch):
    good = (web / "styles.css").read_text(encoding="utf-8").replace("#FF2E88", "#00AACC")
    replies(monkeypatch, "Sure, here you go:\n\n```css\n" + good + "\n```\n\nHope that helps!")

    _, body = engine.post("/upgrade", {"request": "make it blue"})
    assert body.get("ok") is True, body
    written = (web / "styles.css").read_text(encoding="utf-8")
    assert "#00AACC" in written
    assert "Hope that helps" not in written


def test_narration_with_no_code_fence_is_still_stripped(engine, web, monkeypatch):
    """
    The shape that shipped a broken file once: prose, no fence, then the
    real file. Without this the English becomes line one of the stylesheet.
    """
    good = (web / "styles.css").read_text(encoding="utf-8").replace("#FF2E88", "#123456")
    replies(monkeypatch,
            "I bumped the accent colour as you asked. Here's the complete file:\n\n" + good)

    _, body = engine.post("/upgrade", {"request": "change the accent colour"})
    assert body.get("ok") is True, body
    written = (web / "styles.css").read_text(encoding="utf-8")
    assert written.lstrip().startswith((":root", "/*"))
    assert "I bumped" not in written


# ── backup and undo ───────────────────────────────────────────────────

def test_a_good_change_is_written_and_can_be_undone(engine, web, monkeypatch):
    original = (web / "styles.css").read_text(encoding="utf-8")
    replies(monkeypatch, original.replace("#FF2E88", "#00FF00"))

    _, body = engine.post("/upgrade", {"request": "make the accent green"})
    assert body["ok"] is True
    assert "#00FF00" in (web / "styles.css").read_text(encoding="utf-8")

    status, undo = engine.post("/upgrade/undo", {})
    assert status == 200
    assert (web / "styles.css").read_text(encoding="utf-8") == original


def test_undo_with_nothing_to_undo_says_so(engine, web):
    status, body = engine.post("/upgrade/undo", {})
    assert status == 200
    assert "error" in body or body.get("ok") is False


def test_a_backup_is_taken_before_anything_is_written(engine, web, monkeypatch, tmp_path):
    original = (web / "styles.css").read_text(encoding="utf-8")
    replies(monkeypatch, original.replace("#FF2E88", "#ABCDEF"))
    engine.post("/upgrade", {"request": "tweak the colour"})

    backups = list((tmp_path / "upgrade_backups").glob("*-styles.css"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original


# ── asking for a change from ordinary chat ────────────────────────────

def test_asking_in_normal_chat_performs_the_change(engine, web, monkeypatch):
    """RJ's requirement: any chat box can change the site, not just Upgrade."""
    original = (web / "styles.css").read_text(encoding="utf-8")
    calls = []

    def fake(system, history, message):
        calls.append(message)
        if len(calls) == 1:
            return ('On it.\n{"upgrade": "make the accent colour purple"}')
        return original.replace("#FF2E88", "#800080")

    monkeypatch.setattr(server, "_chat_claude_cli", fake)
    server.ENV["CHAT_PROVIDER"] = "claude-cli"

    _, body = engine.post("/chat", {"message": "can you make the accent purple",
                                    "history": []})
    assert "#800080" in (web / "styles.css").read_text(encoding="utf-8")
    assert "Updated" in body["reply"] or "reload" in body["reply"].lower()


def test_an_ordinary_question_does_not_rewrite_anything(engine, web, monkeypatch):
    before = {f.name: f.read_text(encoding="utf-8") for f in web.iterdir()}
    monkeypatch.setattr(server, "_chat_claude_cli",
                        lambda *a, **k: "Onoway Fire is worth applying to first.")
    server.ENV["CHAT_PROVIDER"] = "claude-cli"

    engine.post("/chat", {"message": "where should I apply first?", "history": []})

    after = {f.name: f.read_text(encoding="utf-8") for f in web.iterdir()}
    assert before == after
