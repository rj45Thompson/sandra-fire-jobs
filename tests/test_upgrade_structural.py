"""
Structural changes, the self-test that gates them, and the way back.

Three things are being pinned down here, and the third is the one that
makes the other two safe to have at all:

  1. A request that changes the SHAPE of the app - moving a control,
     adding a step - can touch index.html and app.js together, because
     neither half works without the other.
  2. After any change, the real page is opened and used. A file that
     parses can still render a dead page, and that is the failure the
     static guards were always blind to.
  3. If the page comes back broken, everything goes back automatically,
     and there is a shipped default underneath that as a last resort.

The browser is mocked here (the real thing is exercised in
test_selftest_real.py) so these run in seconds rather than minutes.
"""

import pytest

import server


@pytest.fixture
def web(tmp_path, monkeypatch):
    """A throwaway front-end, including the shipped defaults to restore from."""
    d = tmp_path / "web"
    (d / "_defaults").mkdir(parents=True)

    html = ('<!doctype html><html><head><link rel="stylesheet" href="styles.css">'
            '</head><body><div id="nav"></div><div id="gap-list"></div>'
            '<div id="settings"></div><div id="cert-list"></div>'
            '<script src="app.js"></script></body></html>')
    js = ("/* app */\nfunction loadApps() { return 1; }\n"
          "function loadGaps() { return 2; }\nconst a = $('#nav');\n"
          "const b = $('#settings');\n" + "// pad\n" * 60)
    css = (":root{ --pink:#FF2E88; --cream:#FFF8F0; --ink:#2B1B2E; }\n"
           ".card{ padding:1rem; }\n" + ".x{color:red}\n" * 60)

    for name, text in (("index.html", html), ("app.js", js), ("styles.css", css)):
        (d / name).write_text(text, encoding="utf-8")
        (d / "_defaults" / name).write_text(text, encoding="utf-8")

    monkeypatch.setattr(server, "WEB_DIR", d)
    return d


def assistant_returns(monkeypatch, text):
    monkeypatch.setattr(server, "_chat_claude_cli", lambda *a, **k: text)


def selftest(monkeypatch, ok, failures=()):
    monkeypatch.setattr(
        server.Handler, "_self_test",
        lambda self: {"ok": ok, "skipped": False,
                      "checks": ["the page loads"] if ok else [],
                      "failures": list(failures)})


# ── structural requests reach the structural path ─────────────────────

@pytest.mark.parametrize("request_text", [
    "move the settings button to the top",
    "put the settings button at the top of the page",
    "add another confirmation step before it sends",
    "I want a final review confirmation step",
    "add a new tab for interview notes",
    "rearrange the dashboard so deadlines come first",
])
def test_shape_changing_requests_are_treated_as_structural(engine, web, monkeypatch,
                                                           request_text):
    seen = {}

    def capture(brief, history, message):
        seen["brief"] = brief
        return ("=== index.html ===\n" + (web / "index.html").read_text(encoding="utf-8")
                + "\n=== app.js ===\n" + (web / "app.js").read_text(encoding="utf-8"))

    monkeypatch.setattr(server, "_chat_claude_cli", capture)
    selftest(monkeypatch, True)

    status, body = engine.post("/upgrade", {"request": request_text})
    assert body.get("structural") is True, body
    # it must be given both files to work from, or it cannot keep them in step
    assert "current index.html" in seen["brief"]
    assert "current app.js" in seen["brief"]


def test_a_plain_colour_change_is_not_treated_as_structural(engine, web, monkeypatch):
    assistant_returns(monkeypatch,
                      (web / "styles.css").read_text(encoding="utf-8")
                      .replace("#FF2E88", "#0088FF"))
    selftest(monkeypatch, True)

    _, body = engine.post("/upgrade", {"request": "make the accent colour blue"})
    assert body.get("structural") is not True
    assert body["file"] == "styles.css"


def test_both_files_are_written_as_one_change(engine, web, monkeypatch):
    new_html = (web / "index.html").read_text(encoding="utf-8").replace(
        '<div id="settings"></div>', '<div id="settings" class="top"></div>')
    new_js = (web / "app.js").read_text(encoding="utf-8") + "\n// moved settings up\n"
    assistant_returns(monkeypatch,
                      f"=== index.html ===\n{new_html}\n=== app.js ===\n{new_js}")
    selftest(monkeypatch, True)

    _, body = engine.post("/upgrade", {"request": "move the settings button to the top"})
    assert body["ok"] is True
    assert 'class="top"' in (web / "index.html").read_text(encoding="utf-8")
    assert "moved settings up" in (web / "app.js").read_text(encoding="utf-8")


# ── the self-test gates it ────────────────────────────────────────────

def test_a_change_that_breaks_the_page_is_put_back_automatically(engine, web, monkeypatch):
    """The whole point: she never ends up looking at a broken page."""
    before_html = (web / "index.html").read_text(encoding="utf-8")
    before_js = (web / "app.js").read_text(encoding="utf-8")

    new_html = before_html.replace('<div id="nav"></div>', '<div id="nav"></div><p>x</p>')
    assistant_returns(monkeypatch,
                      f"=== index.html ===\n{new_html}\n=== app.js ===\n{before_js}")
    selftest(monkeypatch, False, ["Clicking profile did not open its panel."])

    _, body = engine.post("/upgrade", {"request": "move the settings button to the top"})

    assert body.get("rolled_back") is True
    assert "put everything back" in body["error"]
    assert "did not open its panel" in body["error"]
    assert (web / "index.html").read_text(encoding="utf-8") == before_html
    assert (web / "app.js").read_text(encoding="utf-8") == before_js


def test_a_single_file_change_that_breaks_the_page_is_also_put_back(engine, web, monkeypatch):
    before = (web / "styles.css").read_text(encoding="utf-8")
    assistant_returns(monkeypatch, before.replace("#FF2E88", "#111111"))
    selftest(monkeypatch, False, ["JavaScript errors on the page: boom"])

    _, body = engine.post("/upgrade", {"request": "make the accent darker"})

    assert body.get("rolled_back") is True
    assert (web / "styles.css").read_text(encoding="utf-8") == before


def test_a_rolled_back_change_does_not_leave_a_backup_behind(engine, web, monkeypatch, tmp_path):
    """Otherwise Undo would 'restore' a change that was never applied."""
    before = (web / "styles.css").read_text(encoding="utf-8")
    assistant_returns(monkeypatch, before.replace("#FF2E88", "#222222"))
    selftest(monkeypatch, False, ["broken"])

    engine.post("/upgrade", {"request": "make the accent darker"})

    backups = list((tmp_path / "upgrade_backups").glob("*")) \
        if (tmp_path / "upgrade_backups").exists() else []
    assert backups == []


def test_a_change_that_passes_is_kept_and_says_it_was_checked(engine, web, monkeypatch):
    before = (web / "styles.css").read_text(encoding="utf-8")
    assistant_returns(monkeypatch, before.replace("#FF2E88", "#00CC88"))
    selftest(monkeypatch, True)

    _, body = engine.post("/upgrade", {"request": "make the accent green"})
    assert body["ok"] is True
    assert body["tested"] is True
    assert "checked it" in body["message"]
    assert "#00CC88" in (web / "styles.css").read_text(encoding="utf-8")


def test_no_browser_available_does_not_block_a_good_change(engine, web, monkeypatch):
    """Refusing a fine change because Playwright is missing would be worse."""
    before = (web / "styles.css").read_text(encoding="utf-8")
    assistant_returns(monkeypatch, before.replace("#FF2E88", "#AA00AA"))
    monkeypatch.setattr(server.Handler, "_self_test",
                        lambda self: {"ok": True, "skipped": True,
                                      "checks": [], "failures": []})

    _, body = engine.post("/upgrade", {"request": "make the accent purple"})
    assert body["ok"] is True
    assert body["tested"] is False
    assert "give it a look" in body["message"]


# ── static guards still apply to structural changes ───────────────────

def test_markup_and_code_that_disagree_are_refused(engine, web, monkeypatch):
    """
    The specific way a structural change goes wrong: the id is renamed in
    one file and not the other, so the page loads and does nothing.
    """
    before_html = (web / "index.html").read_text(encoding="utf-8")
    new_html = before_html.replace('id="settings"', 'id="settings-moved"')
    new_js = (web / "app.js").read_text(encoding="utf-8")   # still looks for #settings

    assistant_returns(monkeypatch,
                      f"=== index.html ===\n{new_html}\n=== app.js ===\n{new_js}")
    selftest(monkeypatch, True)

    _, body = engine.post("/upgrade", {"request": "move the settings button to the top"})
    assert "error" in body
    assert "not in the page" in body["error"]
    assert (web / "index.html").read_text(encoding="utf-8") == before_html


def test_structural_javascript_that_does_not_parse_is_refused(engine, web, monkeypatch):
    before_js = (web / "app.js").read_text(encoding="utf-8")
    assistant_returns(monkeypatch,
                      "=== app.js ===\n" + before_js + "\nfunction broken( {\n")
    selftest(monkeypatch, True)

    _, body = engine.post("/upgrade", {"request": "add a new tab for notes"})
    assert "error" in body
    assert "does not parse" in body["error"]
    assert (web / "app.js").read_text(encoding="utf-8") == before_js


def test_a_reply_in_the_wrong_shape_changes_nothing(engine, web, monkeypatch):
    before = {f.name: f.read_text(encoding="utf-8")
              for f in web.iterdir() if f.is_file()}
    assistant_returns(monkeypatch, "Sure, I would move the settings button up top.")
    selftest(monkeypatch, True)

    _, body = engine.post("/upgrade", {"request": "move the settings button to the top"})
    assert "error" in body
    after = {f.name: f.read_text(encoding="utf-8")
             for f in web.iterdir() if f.is_file()}
    assert before == after


# ── the floor: restore what shipped ───────────────────────────────────

def test_restoring_the_original_design_undoes_everything(engine, web, monkeypatch):
    original = (web / "styles.css").read_text(encoding="utf-8")

    # drift a long way from the original, across several changes
    for colour in ("#111111", "#222222", "#333333"):
        assistant_returns(monkeypatch, original.replace("#FF2E88", colour))
        selftest(monkeypatch, True)
        engine.post("/upgrade", {"request": f"make the accent {colour}"})
    assert "#333333" in (web / "styles.css").read_text(encoding="utf-8")

    status, body = engine.post("/upgrade/reset", {})
    assert status == 200
    assert body["ok"] is True
    assert "styles.css" in body["restored"]
    assert (web / "styles.css").read_text(encoding="utf-8") == original


def test_restore_works_even_with_no_backup_history(engine, web, tmp_path):
    """Undo needs history; the floor must not."""
    (web / "styles.css").write_text("/* wrecked */", encoding="utf-8")
    backups = tmp_path / "upgrade_backups"
    if backups.exists():
        for f in backups.iterdir():
            f.unlink()

    status, body = engine.post("/upgrade/reset", {})
    assert body["ok"] is True
    restored = (web / "styles.css").read_text(encoding="utf-8")
    assert "--pink" in restored
    assert "wrecked" not in restored


def test_restoring_keeps_a_copy_in_case_that_was_the_mistake(engine, web, tmp_path):
    hers = (web / "styles.css").read_text(encoding="utf-8").replace("#FF2E88", "#ABCDEF")
    (web / "styles.css").write_text(hers, encoding="utf-8")

    engine.post("/upgrade/reset", {})

    saved = list((tmp_path / "upgrade_backups").glob("*-styles.css"))
    assert saved, "her version should still be recoverable"
    assert "#ABCDEF" in saved[0].read_text(encoding="utf-8")


def test_restoring_when_nothing_changed_says_so(engine, web):
    status, body = engine.post("/upgrade/reset", {})
    assert body["ok"] is True
    assert body["restored"] == []
    assert "already" in body["message"]


def test_restore_reports_honestly_if_the_defaults_are_gone(engine, web):
    for f in (web / "_defaults").iterdir():
        f.unlink()
    status, body = engine.post("/upgrade/reset", {})
    assert "error" in body


# ── the self-test is available on its own ─────────────────────────────

def test_the_page_can_be_checked_without_changing_anything(engine, web, monkeypatch):
    selftest(monkeypatch, True)
    status, body = engine.post("/upgrade/selftest", {})
    assert status == 200
    assert body["ok"] is True
