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
  let watcher = null;         // watches for fields that arrive after load

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
    // A multi-step application fills across several passes; undo should still
    // reach back over all of them rather than only the most recent step.
    if (!opts.append) lastFill = [];

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
      seen.add(el);
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

  /* ---------- staying with the form ----------
   *
   * Pressing Fill once should be enough for one application. Three things
   * otherwise make it not enough, and they are the same problem wearing
   * different hats: the fields are not on the page yet.
   *
   *   1. A login is in the way. The extension does not try to get through it —
   *      it has no credentials and wants none. The person signs in themselves,
   *      exactly as they would anyway, and we pick the work back up. Waiting is
   *      the feature: it is the difference between automating an application
   *      and holding someone's password.
   *   2. The form has not hydrated. Greenhouse and Workday render an empty
   *      shell first, so a Fill pressed a second too early finds nothing at all.
   *   3. The application has more than one step. Workday's "Next" swaps the
   *      whole form for new fields that also want filling.
   *
   * So one observer handles all three: watch, settle, scan, fill what is new
   * and still empty. Nothing already typed is touched, because run() skips a
   * field that has a value unless explicitly told otherwise.
   */

  const LOGIN_LIMIT = 10 * 60 * 1000;   // people go and find their password
  const HYDRATE_LIMIT = 45 * 1000;      // a form that never appears, will not
  const FOLLOW_LIMIT = 10 * 60 * 1000;  // later steps of the same application
  const SETTLE = 600;                   // let a re-render finish before looking

  const seen = new WeakSet();           // elements we have already filled

  function stopWatching() {
    if (watcher) {
      watcher.observer.disconnect();
      clearTimeout(watcher.timer);
      clearTimeout(watcher.deadline);
      watcher = null;
    }
  }

  /** Watch the page and fill whatever turns up.
   *  mode: "login" | "hydrate" | "follow" — differs only in how long we wait
   *  and what the banner says when it finally works. */
  function watch(profile, opts, mode) {
    stopWatching();
    const limit = mode === "login" ? LOGIN_LIMIT
                : mode === "hydrate" ? HYDRATE_LIMIT
                : FOLLOW_LIMIT;

    const attempt = () => {
      if (!watcher) return;
      watcher.timer = null;

      // Still behind the login: nothing to do but keep waiting.
      if (mode === "login" && ApplyFields.authWall().wall) return;

      const fresh = ApplyFields.scan().filter(
        (f) => !seen.has(f.el) && (f.kind === "file" || !f.el.value));
      if (!fresh.length) return;

      const report = run(profile, Object.assign({}, opts, { append: true }));
      if (!report.filled) return;
      report.resumed = mode === "login";
      report.followed = mode === "follow";
      banner(report);

      // A login only comes down once; later steps can keep coming.
      if (mode !== "follow") { stopWatching(); watch(profile, opts, "follow"); }
    };

    const ping = () => {
      if (!watcher) return;
      clearTimeout(watcher.timer);
      watcher.timer = setTimeout(attempt, SETTLE);
    };

    const observer = new MutationObserver(ping);
    observer.observe(document.documentElement, { childList: true, subtree: true });
    watcher = { observer, timer: null, ping,
                deadline: setTimeout(stopWatching, limit) };
  }

  /* A single-page application can change route without touching much of the
   * DOM first, so the observer may not fire until after the new step renders.
   * Nudging it on a navigation makes the next step feel immediate rather than
   * arriving a beat late. */
  function watchRouteChanges() {
    const nudge = () => { if (watcher) watcher.ping(); };
    for (const name of ["pushState", "replaceState"]) {
      const original = history[name];
      history[name] = function (...args) {
        const out = original.apply(this, args);
        nudge();
        return out;
      };
    }
    window.addEventListener("popstate", nudge);
  }
  watchRouteChanges();

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
      const lead = report.resumed ? "Signed in — filled"
                 : report.followed ? "New step — filled"
                 : "Filled";
      msg = `${lead} ${report.filled} field${report.filled === 1 ? "" : "s"}. `
          + "Check them, then submit yourself.";
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
      const opts = { overwrite: msg.overwrite };
      if (report.filled === 0 && report.wall) {
        watch(msg.profile, opts, "login");        // they sign in, we resume
        report.waiting = true;
      } else if (report.filled === 0) {
        watch(msg.profile, opts, "hydrate");      // the form is still rendering
        report.waiting = true;
      } else {
        watch(msg.profile, opts, "follow");       // later steps of this form
      }
      respond(report);
    } else if (msg.type === "AA_SCAN") {
      const found = ApplyFields.scan();
      respond({ count: found.length, keys: [...new Set(found.map((f) => f.key))],
                wall: ApplyFields.authWall().wall });
    } else if (msg.type === "AA_UNDO") {
      stopWatching();
      undo();
      respond({ ok: true });
    }
    return true;                       // keep the channel open for the async respond
  });
})();
