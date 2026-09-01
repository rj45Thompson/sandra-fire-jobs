/* Apply Assist — the part that actually fills the form.
 *
 * Two things here are load-bearing and easy to get wrong:
 *
 * 1. Greenhouse, Lever, Ashby and Workday are React applications. Assigning
 *    `el.value = x` updates the DOM but NOT React's internal value tracker, so
 *    React overwrites it on the next render and the field silently empties on
 *    submit. The fix is to call the native property setter from the prototype,
 *    which is what a real keystroke does, then dispatch the events React listens
 *    for. `setNativeValue` below is that fix.
 *
 * 2. Nothing here submits. The extension fills and then gets out of the way.
 *    That is partly respect for the applicant, and partly that auto-submitting
 *    is exactly the behaviour application portals fingerprint as a bot.
 */

(() => {
  const FILLED = "aa-filled";
  let lastFill = [];          // for undo
  let waiting = null;         // MutationObserver, while a sign-in is in progress

  /* ---------- writing a value the way a keystroke would ---------- */

  function setNativeValue(el, value) {
    const proto = Object.getPrototypeOf(el);
    const desc = Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) desc.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function fillText(el, value) {
    const before = el.value;
    el.focus({ preventScroll: true });
    setNativeValue(el, value);
    el.blur();
    return before;
  }

  /** Selects need the option matched by visible text, not by our raw value. */
  function fillSelect(el, value) {
    const before = el.value;
    const want = String(value).toLowerCase().trim();
    let hit = null;

    for (const opt of el.options) {
      const text = opt.textContent.toLowerCase().trim();
      const val = String(opt.value).toLowerCase().trim();
      if (text === want || val === want) { hit = opt; break; }
    }
    if (!hit) {
      for (const opt of el.options) {
        const text = opt.textContent.toLowerCase().trim();
        if (!text || text.startsWith("select") || text.startsWith("--")) continue;
        if (text.includes(want) || want.includes(text)) { hit = opt; break; }
      }
    }
    if (!hit) return null;

    setNativeValue(el, hit.value);
    return before;
  }

  /** Yes/no questions render as radios far more often than as selects. */
  function fillRadioGroup(el, value) {
    if (!el.name) return null;
    const want = String(value).toLowerCase().trim();
    const group = document.querySelectorAll(
      `input[type="radio"][name="${CSS.escape(el.name)}"]`);
    for (const r of group) {
      const label = (ApplyFields.signature(r) || "").toLowerCase();
      const val = String(r.value).toLowerCase();
      if (val === want || label.includes(want)) {
        const before = group_checked(group);
        r.click();
        return before;
      }
    }
    return null;
  }

  const group_checked = (group) => {
    for (const r of group) if (r.checked) return r.value;
    return "";
  };

  /* ---------- attaching a file ---------- */

  /* A file input cannot be set with `el.value = path` — browsers forbid it, and
   * rightly so. The supported route is to build a real File and hand it over via
   * a DataTransfer, which is what a drag-and-drop does. This is the whole reason
   * the resume has to live somewhere the extension can read bytes from: without
   * the bytes there is nothing to attach, and an application with no resume is
   * not an application. */
  function attachFile(el, spec) {
    if (!spec || !spec.data || !spec.name) return null;
    try {
      // base64 -> Uint8Array, without pulling in a dependency.
      const bin = atob(spec.data);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);

      const file = new File([bytes], spec.name, {
        type: spec.mimeType || "application/pdf",
        lastModified: Date.now()
      });

      const dt = new DataTransfer();
      dt.items.add(file);
      el.files = dt.files;

      // Same event pair as a typed field: React and friends listen for these.
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return "";                       // previous state: no file
    } catch {
      return null;                     // DataTransfer unavailable, or bad base64
    }
  }

  /* ---------- resolving a profile key to a string ---------- */

  function valueFor(profile, key) {
    if (key === "fullName") {
      if (profile.fullName) return profile.fullName;
      const joined = [profile.firstName, profile.lastName].filter(Boolean).join(" ");
      return joined || null;
    }
    const v = profile[key];
    return (v === undefined || v === null || v === "") ? null : String(v);
  }

  /* ---------- the run ---------- */

  function run(profile, opts = {}) {
    const found = ApplyFields.scan();
    const report = { filled: 0, skipped: 0, fields: [], wall: ApplyFields.authWall().wall };
    lastFill = [];

    for (const { el, key, kind } of found) {
      if (kind === "file") {
        // Never replace a file the person already chose.
        if (el.files && el.files.length) { report.skipped++; continue; }
        const spec = profile[key];
        const before = attachFile(el, spec);
        if (before === null) { report.skipped++; continue; }
        lastFill.push({ el, before, type: "file", name: el.name });
        el.classList.add(FILLED);
        report.filled++;
        report.fields.push(key);
        continue;
      }

      const value = valueFor(profile, key);
      if (value === null) { report.skipped++; continue; }

      // Don't clobber something the person already typed.
      if (!opts.overwrite && el.value && el.type !== "radio") {
        report.skipped++;
        continue;
      }

      let before = null;
      if (el.tagName === "SELECT") before = fillSelect(el, value);
      else if (el.type === "radio") before = fillRadioGroup(el, value);
      else before = fillText(el, value);

      if (before === null) { report.skipped++; continue; }

      lastFill.push({ el, before, type: el.type, name: el.name });
      el.classList.add(FILLED);
      report.filled++;
      report.fields.push(key);
    }
    return report;
  }

  function undo() {
    for (const rec of lastFill) {
      try {
        if (rec.type === "file") {
          rec.el.value = "";           // permitted: clearing a file input is allowed
          rec.el.dispatchEvent(new Event("change", { bubbles: true }));
        } else if (rec.type === "radio") {
          const group = document.querySelectorAll(
            `input[type="radio"][name="${CSS.escape(rec.name)}"]`);
          group.forEach((r) => { r.checked = String(r.value) === String(rec.before); });
          group.forEach((r) => r.dispatchEvent(new Event("change", { bubbles: true })));
        } else {
          setNativeValue(rec.el, rec.before);
        }
        rec.el.classList.remove(FILLED);
      } catch { /* the page may have re-rendered it away; nothing to undo */ }
    }
    lastFill = [];
  }

  /* ---------- signing in ----------
   *
   * Plenty of applications sit behind a login, and LinkedIn and Workday put one
   * in front of almost everything. The extension does not try to get through
   * it. It has no credentials, wants none, and a tool that collects them is a
   * tool nobody should install.
   *
   * So the handoff is: the person signs in themselves, in their own browser,
   * exactly as they would anyway. We watch for the wall to come down and pick
   * the work back up. Waiting is a feature — it is the difference between
   * automating someone's application and holding their password. */

  const WAIT_LIMIT = 10 * 60 * 1000;

  function stopWaiting() {
    if (waiting) { waiting.disconnect(); waiting = null; }
  }

  function waitForLogin(profile, opts) {
    stopWaiting();
    const started = Date.now();
    let timer = null;

    const attempt = () => {
      timer = null;
      if (Date.now() - started > WAIT_LIMIT) { stopWaiting(); return; }
      if (ApplyFields.authWall().wall) return;      // still signing in
      const found = ApplyFields.scan();
      if (!found.length) return;                    // through, but no form yet
      stopWaiting();
      const report = run(profile, opts);
      report.resumed = true;
      banner(report);
    };

    waiting = new MutationObserver(() => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(attempt, 600);             // let the page settle first
    });
    waiting.observe(document.documentElement, { childList: true, subtree: true });
    setTimeout(stopWaiting, WAIT_LIMIT);
  }

  /* ---------- the little banner ---------- */

  function banner(report) {
    document.querySelector("#aa-banner")?.remove();
    const b = document.createElement("div");
    b.id = "aa-banner";

    let msg;
    if (report.filled === 0 && report.wall) {
      msg = "Sign in yourself and I will carry on. Apply Assist never sees your "
          + "password — it is waiting, not watching.";
    } else if (report.filled === 0) {
      msg = "Nothing matched on this page.";
    } else {
      msg = `${report.resumed ? "Signed in — filled" : "Filled"} ${report.filled} `
          + `field${report.filled === 1 ? "" : "s"}. Check them, then submit yourself.`;
      if (report.wall) msg += " There is still a sign-in on this page.";
    }

    const text = document.createElement("span");
    text.className = "aa-msg";
    text.textContent = msg;

    const undoBtn = document.createElement("button");
    undoBtn.className = "aa-btn";
    undoBtn.textContent = "Undo";
    undoBtn.onclick = () => { undo(); b.remove(); };

    const close = document.createElement("button");
    close.className = "aa-x";
    close.setAttribute("aria-label", "Dismiss");
    close.textContent = "✕";
    close.onclick = () => b.remove();

    b.append(text);
    if (report.filled) b.append(undoBtn);
    b.append(close);
    document.body.append(b);
    setTimeout(() => b.remove(), 12000);
  }

  /* ---------- messages from the popup ---------- */

  chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
    if (msg.type === "AA_FILL") {
      const report = run(msg.profile, { overwrite: msg.overwrite });
      banner(report);
      // Nothing to fill because there is a login in the way: wait it out rather
      // than making the person come back and press the button again.
      if (report.filled === 0 && report.wall) {
        waitForLogin(msg.profile, { overwrite: msg.overwrite });
        report.waiting = true;
      }
      respond(report);
    } else if (msg.type === "AA_SCAN") {
      const found = ApplyFields.scan();
      respond({ count: found.length, keys: [...new Set(found.map((f) => f.key))],
                wall: ApplyFields.authWall().wall });
    } else if (msg.type === "AA_UNDO") {
      stopWaiting();
      undo();
      respond({ ok: true });
    }
    return true;                       // keep the channel open for the async respond
  });
})();
