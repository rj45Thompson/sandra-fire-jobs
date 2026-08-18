# Muster — Sandra's firefighter job engine

A local-first application system for firefighting work in Alberta and beyond.
Pink, modern, and built to actually reach every department that hires.

## Why this shape

Firefighter hiring is nothing like software hiring:

- Postings live on **municipal HR portals** (NEOGOV/GovernmentJobs, Workday,
  ApplicantPro), not on one job board you can scrape once.
- Many departments run **eligibility pools** and hiring **windows** — timing
  matters more than volume. Missing a window costs a year.
- Applications demand **certification documents with expiry dates**, a
  driver's abstract, fitness test results, and immunization records. The
  document set is the application.
- Paid-on-call, casual, and full-time are separate tracks with separate forms.

So: a document-and-deadline engine with a chat front-end, not a spray-and-pray
résumé cannon.

## Architecture

```
GitHub Pages  (public, static, zero secrets)
  docs/  ── the front-end: chat, profile intake, résumé upload,
            application tracker, deadline calendar
        │
        │  HTTPS page → http://127.0.0.1:8770  (browsers permit
        │  localhost as a trusted origin, so no mixed-content block)
        ▼
Local backend  (your machine, holds everything private)
  backend/  ── FastAPI-free stdlib HTTP server
              • gmail_client.py   IMAP via App Password
              • boards.py         per-employer source adapters
              • applier.py        Playwright form filling
              • chat.py           Ollama (local) or Claude API
              • store.py          SQLite
  data/     ── profile, résumés, certs, chat log  [all gitignored]
  .env      ── credentials only                   [gitignored]
```

The public site never holds a secret. The private data never leaves the house.

## Burn-down

### M1 — Skeleton that runs  ✅ target: tonight
- [x] Repo, gitignore hardened for PII + credentials
- [ ] Front-end shell: Barragán/rosa-mexicano theme, all 5 panels
- [ ] Backend HTTP server + token auth + CORS for the Pages origin
- [ ] SQLite schema: profile, documents, employers, postings, applications, events
- [ ] `/health` handshake so the site shows "connected"
- [ ] Push, enable GitHub Pages

### M2 — Sandra's data in
- [ ] Profile intake form: the full firefighter field schema
- [ ] Résumé upload + parse into structured fields
- [ ] Certification registry with **expiry tracking + renewal alerts**
- [ ] Document vault: NFPA 1001, EMR, Class 3/air brakes, abstract, fit test,
      immunization, criminal record check

### M3 — Reach every employer
- [ ] Employer registry seeded from research (municipal + industrial + wildland)
- [ ] Source adapters per ATS family (NEOGOV, Workday, ApplicantPro, email)
- [ ] Poller: new-posting detection, dedupe, match-scoring vs her certs
- [ ] Hiring-window calendar: which departments open when

### M4 — Apply
- [ ] Playwright filler per ATS family, using the intake data
- [ ] Review queue — nothing submits unreviewed while `AUTO_SUBMIT=false`
- [ ] Attach correct document set per employer
- [ ] Log every submission with a receipt

### M5 — Email loop
- [ ] IMAP scan for replies, interview invites, rejections
- [ ] Auto-classify and thread onto the right application
- [ ] Surface "needs your answer" items to the top of the dashboard

### M6 — Chat
- [ ] HTTP chat endpoint, streamed
- [ ] Grounded in her profile + application history
- [ ] Can draft cover letters and answer "what should I do next"

### M7 — The certification gap
- [ ] Track the NFPA 1001 live-fire outstanding item as a first-class blocker
- [ ] Registry of academies/host departments that take partial-completion
      candidates, with contacts and next intake dates

## Non-goals
- No credential harvesting. App Password or OAuth, local only.
- No auto-submitting garbage. Quality over count — fire departments talk to
  each other and a sloppy application is remembered.
