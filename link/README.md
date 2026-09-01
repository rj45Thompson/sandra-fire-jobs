# Putting the assistant on the public page

The page is static HTML on GitHub Pages. Claude does not live there, so the
chat has to reach a machine where Claude is signed in — yours. Nothing about
that needs an API key: the desktop bridge shells out to the Claude Code CLI
that is already logged in, so answers come off your own subscription.

    the page  ──►  a tunnel  ──►  your desktop  ──►  claude -p --model opus

## Once

Make a token so the desk can tell the page where it is. GitHub → Settings →
Developer settings → **Fine-grained tokens**: this repository only, **Contents:
read and write**. Put it in `.env` beside the bridge:

    GITHUB_TOKEN=github_pat_...

That token stays on your machine. It is used to write one file — `relay.json`
next to the page — and for nothing else.

## Every time

    powershell -ExecutionPolicy Bypass -File bridge_up.ps1

That starts the engine, opens a tunnel, and publishes the address itself.
There is nothing to copy and nothing to send anyone. Give GitHub Pages about
half a minute and the public page is answering from your machine; a page
someone already had open connects on its own without a reload.

Close the window and the address is cleared, so the page says the assistant is
not up rather than hanging on an address that has gone.

## Why the address kept having to be carried by hand

A quick tunnel is anonymous, which is the good part, and gets a new random
hostname every run, which is the bad part. `publish_address.py` closes that
gap: the desk writes its own address into `relay.json`, and the page re-reads
that file every twenty seconds.

## What is actually exposed

One endpoint, `/bridge/chat`: chat text in, chat text out, behind the same
access code as the page. The profile, the applications, the mailbox and the
applier stay behind the home-network rule and are not reachable from outside.

    python link/test/publish.test.py     # 10 tests, no network
