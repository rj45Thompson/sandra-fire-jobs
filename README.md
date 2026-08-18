# Muster

A local-first job engine for the fire service, built for one firefighter in
Alberta who was tired of applications disappearing into portals.

**Front-end:** https://rj45thompson.github.io/sandra-fire-jobs/

## What it does

- Watches every fire department, industrial responder and wildland employer
  in the registry for new postings
- Tracks certifications and warns before one expires, because a lapsed cert
  silently disqualifies an application
- Fills applications and queues them for review - nothing submits unreviewed
- Watches email for replies and threads them onto the right application
- A chat that knows her profile, her certs and what is currently open

## The split

The public site holds no secrets and no personal data. It is a face.
Everything private lives on the local engine on her machine.

```
GitHub Pages (public, static)  →  http://127.0.0.1:8770  (local, private)
      docs/                              backend/  data/  .env
```

## Run it

```bash
cp config.example.env .env      # then fill in the app password + token
py backend/server.py
```

Open `docs/index.html` (or the Pages URL), click **Connect**, paste the token.

## Credentials

Google has not accepted account passwords for mail since 2022. Use a Gmail
**App Password** - Google Account → Security → 2-Step Verification → App
passwords. It lives in `.env`, which is gitignored, and it can be revoked from
that same page at any time. It cannot be used to sign in to the account itself.

Nothing in `data/` or `.env` is ever committed.
