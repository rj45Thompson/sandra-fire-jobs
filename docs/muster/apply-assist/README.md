# Apply Assist

A browser extension that fills job application forms from a profile you save
once. It runs inside your own browser session, so it never needs a password.

## Why an extension and not a bot

The obvious design — a server that logs into job sites as the user — requires
collecting other people's credentials. That is the single worst thing a small
service can hold: 2FA defeats it anyway, and one breach is unrecoverable.

An extension sidesteps the whole problem. It runs in the browser where the
person is *already logged in*, as them, with no credential ever leaving their
machine. It is also one codebase for Windows, macOS and Linux, because Chrome
does the platform work. Chrome, Edge, Brave and Opera take the same package.

## What it will and will not do

**Will**: recognise the standard application fields across the major applicant
tracking systems, fill them from your saved profile, highlight what it touched,
and offer a one-click undo.

**Will not**: ask for a password, submit an application, or send your details
anywhere. There is no account and no backend. Your profile lives in
`chrome.storage.local` on your own computer.

It also refuses to fill some things on purpose. Passwords, payment details,
SIN/SSN, and every EEO/demographic question (gender, race, veteran, disability,
date of birth) are on a permanent deny list. Those answers are yours to give
deliberately, not something a tool should be putting words in your mouth about.

Not submitting is also the pragmatic choice: auto-submission is the behaviour
application portals fingerprint as a bot.

## Install

Not on the Web Store yet. To run it now:

1. Open `chrome://extensions`
2. Turn on **Developer mode**
3. **Load unpacked** and select this `extension/` folder
4. The options page opens on first install — fill in what you want and Save

## Use

Open a job application, click the toolbar icon, and press **Fill this page**.
Filled fields get a green outline. Review them, fix anything it got wrong, and
submit yourself.

By default it will not overwrite a field you already typed into; there is a
checkbox in the popup if you want it to.

## How detection works

`src/fields.js` scores each input against every known profile key rather than
taking the first regex that hits, because forms label the same field a dozen
ways. Signals in order of authority:

1. `autocomplete` — when a site sets it, it is definitive
2. the visible `<label>` — what a human reads
3. `name` / `id` — what the developer called it
4. `placeholder` / `aria-label` — fallback

Each rule also carries a veto list, which is what keeps "first name" out of
"company name" and "salary expectation" separate from "current salary". A field
is only filled when the winning score clears the threshold, so an ambiguous
input is left alone rather than filled with something wrong.

## The React problem

Greenhouse, Lever, Ashby and Workday are React apps. Assigning `el.value = x`
updates the DOM but not React's internal value tracker, so React overwrites it
on the next render and the field is silently empty on submit. `setNativeValue`
in `src/content.js` calls the prototype's native setter — what a real keystroke
does — then dispatches the events React listens for. Without this the extension
appears to work and quietly loses every answer.

## Supported sites

Greenhouse, Lever, Ashby, Workday, SmartRecruiters, BambooHR, iCIMS, Jobvite,
Breezy, Workable, Teamtailor, Recruitee, Personio, SuccessFactors, Taleo.

Anywhere else, the popup injects on demand using `activeTab` — a permission
granted by your click rather than a standing grant over every site you visit.

## Tests

```
node test/detect.test.js
```

35 cases, no framework and nothing to install. The suite is deliberately
weighted toward what must *never* be filled, because the failure that hurts a
user is not a missed field, it is a confident wrong answer in a sensitive box.

## Layout

```
manifest.json          MV3 manifest
src/fields.js          detection: rules, scoring, deny list
src/content.js         filling, React-safe writes, undo, banner
src/background.js      service worker, storage defaults, on-demand injection
src/popup.*            toolbar UI
src/options.*          profile editor
test/detect.test.js    detection tests
```

## Note on LinkedIn

LinkedIn's User Agreement prohibits automated access, and enforcement lands on
the account being automated — the applicant's. Applying through the employer's
own ATS avoids that entirely, which is why the supported list above is ATS
hosts rather than job boards.
