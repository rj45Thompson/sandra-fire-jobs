# Muster — test plan

One command runs everything:

```bash
py run_tests.py
```

183 automated tests: 162 in Python against the engine, 21 in Node against the
front-end. Nothing in the suite touches Sandra's real database, reaches Gmail,
spawns the assistant, or opens a browser unless that is the thing under test.

---

## What this is protecting

Muster is not a toy. If it loses an application, silently fails to submit one,
or hands Sandra a page where the buttons do nothing, she loses a job
opportunity and has no way of knowing it happened. Almost every rule below
exists because something in that family actually went wrong.

Three failure modes drive the whole plan:

| Failure | Why it is the dangerous kind | Where it is covered |
|---|---|---|
| **Silent loss** — an application attempt that vanishes | She cannot act on what she cannot see | `test_apply_journeys.py` |
| **Silent breakage** — a page that loads and does nothing | Nothing looks wrong until a button is clicked | `smoke.py`, `test_upgrade_structural.py` |
| **False confidence** — something that reports success but did not work | Worse than an error, because it stops her checking | `test_connectors_auth.py` |

---

## Layers

### 1. Unit — the logic, no I/O

| File | Covers |
|---|---|
| `test_resume_parsing.py` | Text extraction from .docx / .txt / .pdf, and every way a file can be malformed |
| `test_profile_fill.py` | What is lifted off a résumé, what is never overwritten, cert de-duplication |
| `test_endpoints.py` (scoring) | The match score: baseline, weights, expired certs, 0–100 bounds |

### 2. Integration — real HTTP against a real engine

Each test starts an actual server on an ephemeral port with a throwaway
database, so routing, JSON, status codes and threading are all exercised.

| File | Covers |
|---|---|
| `test_endpoints.py` | Every remaining route, plus malformed input and unknown paths |
| `test_upload_endpoint.py` | Upload → parse → profile fills, including the unparseable file |
| `test_network_access.py` | Who may connect at all (below) |

### 3. Use cases — Sandra actually applying

`test_apply_journeys.py`. Each walks a route she really takes, from posting to
a row she can act on.

- **By web form** — filled, résumé attached, queued for review
- **By LinkedIn** — login wall detected, flagged `needs_you`, never silently skipped
- **A consent box** — surfaced, never ticked on her behalf (only she can certify her own information)
- **A dead posting** — recorded `failed` with the real reason
- **No browser at all** — reported, not crashed
- **By email** — replies classified as interview / rejection / acknowledgement

> **The invariant they share matters more than any one of them:** every attempt
> lands in the applications list with an honest status and a reason. Three
> attempts, three visible rows, no exceptions.

### 4. Connectors and authentication

`test_connectors_auth.py`. Mostly failure paths, because that is where the
damage is.

**Gmail**
- A normal password instead of an App Password → must say *what kind* of password is needed (Gmail's own error does not)
- A rejected credential → never written to `.env`, never shows as connected
- Reconnecting → replaces the old credential rather than stacking
- `/email/status` → never echoes the password back
- Gmail unreachable → reported as a network problem, not a bad password

**The assistant**
- Not installed / not on PATH → explained, not swallowed
- Ollama not running → tells her the command to start it
- Unknown provider → falls back rather than breaking
- Her profile and certificates must reach the prompt, or the advice is generic

### 5. Access control

`test_network_access.py` — 22 tests on who may reach an engine that runs the
assistant and rewrites its own files.

1. **Home network only.** Any address outside real LAN ranges is refused
   outright, correct PIN or not. Ranges are spelled out rather than using
   `ipaddress.is_private`, which counts documentation ranges like
   `203.0.113.0/24` as private. *A test using one of those found that flaw.*
2. **New devices register once with a PIN**, then are remembered by cookie.
   Five wrong tries → 5-minute lockout, per device, so one fumbling phone
   cannot lock out the house.
3. **No PIN set → other devices refused**, not quietly admitted.
4. This machine is never prompted.

### 6. Self-modification

The riskiest surface: a bad change here does not throw, it ships.

`test_self_upgrade.py` — mostly what must be **refused**: JS that does not
parse, a rewrite that drops half the page or the palette, HTML missing an
element the code needs. Plus routing (does a bug report reach `app.js` and a
typo reach `index.html`).

`test_upgrade_structural.py` — multi-file changes, the self-test that gates
them, and the way back.

### 7. The page self-test — `backend/smoke.py`

Every other guard is static and blind to a file that parses and renders a dead
page. This opens the real page in a headless browser and uses it:

- All seven tabs click and their panels open
- Résumé upload and every chat box exist
- Typing into the chat works
- No uncaught JavaScript errors throughout

16 checks. Runs automatically after any self-upgrade — **fail → automatic
rollback**, so Sandra never sees the broken version. Also available as a button
("Check the page works") and standalone:

```bash
py backend/smoke.py
```

### 8. Front-end — Node, no npm dependencies

Uses a hand-rolled DOM in node's `vm`, matching the project's stdlib-only rule.

| File | Covers |
|---|---|
| `test_upload_frontend.js` | Résumé upload refreshes the needs list |
| `test_open_at_home.js` | The public page's redirect to the machine at home |

---

## Verification discipline

**A test that cannot fail is worse than no test** — it converts an unknown into
false confidence. So new safety-critical tests are checked by deliberately
breaking the code and confirming they catch it.

Mutations verified so far:

| Mutation | Caught by |
|---|---|
| Narrow the résumé `except` back to the original | 3 tests |
| Break "never overwrite what she typed" | 2 tests |
| Restore the buggy `lifted_certs` gating | 4 tests |
| Restore the bare `text` upgrade trigger | 1 test |
| Disable the self-test rollback | 1 test |

This has already caught a bad test of my own: one asserted that
`/profile/gaps` had been fetched *at all*, which page load already does, so it
passed even with the bug reintroduced. It now compares before/after counts.

---

## Bugs this suite found

All in the product, none of them theoretical:

1. **Send button dead on 4 of 6 chat boxes** — a dropped argument made it throw
   as an unhandled rejection. Enter worked; clicking never did.
2. **Timestamps six hours out** — raw UTC rendered without conversion.
3. **`watchdog --stop` never stopped anything** — doubled braces made the kill
   a silent no-op while it logged success, so restarts reused stale code.
4. **Four upgrade-routing defects** — `"not updating"` could never match
   (trailing word boundary), `"does nothing"` was missed, `"fix the typo"` went
   to JS, `"make the text bigger"` went to HTML.
5. **Documentation IP ranges treated as home network** — found by a test using
   `203.0.113.7`.
6. **Résumé upload did not refresh the needs list** unless a certification was
   also recognised.

---

## Known gaps

Stated plainly rather than left for someone to discover:

- **No real Gmail account is ever contacted.** IMAP is mocked. Sandra's actual
  App Password has never been entered, so the live path is unproven end to end.
- **No real employer site is submitted to.** `AUTO_SUBMIT` is off by design —
  Muster fills and stops. Journeys mock the browser at `fill_application`.
- **The scan is not tested against live sites**, only mocked and local servers.
- **Visual regressions are not covered.** The self-test proves the page
  *works*, not that it looks right.
- **`test_scan.py` does not exist yet** — scanning is currently only covered
  incidentally.
