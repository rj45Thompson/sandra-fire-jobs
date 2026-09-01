/* Apply Assist — popup.
 * Asks the page what it found, then hands the profile over on a click. */

const $ = (s) => document.querySelector(s);

let tabId = null;
let profile = null;

function showError(msg) {
  const e = $("#err");
  e.textContent = msg;
  e.hidden = false;
}

/** Ask the content script what it sees. If it isn't there (a site outside the
 *  declared host list), inject it first — activeTab makes that the person's
 *  choice rather than a standing permission. */
async function askPage() {
  try {
    return await chrome.tabs.sendMessage(tabId, { type: "AA_SCAN" });
  } catch {
    const res = await chrome.runtime.sendMessage({ type: "AA_INJECT", tabId });
    if (!res?.ok) throw new Error(res?.error || "This page cannot be read.");
    return await chrome.tabs.sendMessage(tabId, { type: "AA_SCAN" });
  }
}

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  tabId = tab?.id;

  const store = await chrome.storage.local.get(["profile", "overwrite"]);
  profile = store.profile || {};
  $("#overwrite").checked = !!store.overwrite;

  const name = profile.fullName ||
    [profile.firstName, profile.lastName].filter(Boolean).join(" ");
  const filled = Object.values(profile).filter((v) => v && String(v).trim()).length;
  $("#who").textContent = name
    ? `${name} · ${filled} fields saved`
    : "No profile yet — add one first";

  if (!tabId || !/^https?:/.test(tab.url || "")) {
    $("#scan").textContent = "Open a job application page to use this.";
    $("#scan").className = "scan none";
    return;
  }

  try {
    const res = await askPage();
    if (!res || !res.count) {
      // A login in the way is a different situation from an empty page, and
      // telling them apart is the difference between "this is broken" and
      // "sign in and I will take it from there".
      $("#scan").textContent = res && res.wall
        ? "Sign in to this site first. Press Fill and I will wait, then finish the form."
        : "No application fields found on this page.";
      $("#scan").className = res && res.wall ? "scan" : "scan none";
      if (res && res.wall) $("#fill").disabled = filled === 0;
      return;
    }
    $("#scan").textContent =
      `${res.count} field${res.count === 1 ? "" : "s"} recognised on this page.`;
    $("#fill").disabled = filled === 0;
    if (filled === 0) showError("Add your details first — nothing to fill with.");
  } catch (e) {
    $("#scan").textContent = "Cannot read this page.";
    $("#scan").className = "scan none";
    showError(String(e.message || e));
  }
}

$("#fill").addEventListener("click", async () => {
  const btn = $("#fill");
  btn.disabled = true;
  btn.textContent = "Filling…";
  try {
    const report = await chrome.tabs.sendMessage(tabId, {
      type: "AA_FILL",
      profile,
      overwrite: $("#overwrite").checked
    });
    btn.textContent = report.filled
      ? `Filled ${report.filled} — check the page`
      : report.waiting ? "Waiting for you to sign in"
      : "Nothing matched";
    setTimeout(() => window.close(), report.waiting ? 1800 : 1100);
  } catch (e) {
    btn.textContent = "Fill this page";
    btn.disabled = false;
    showError(String(e.message || e));
  }
});

$("#overwrite").addEventListener("change", (e) =>
  chrome.storage.local.set({ overwrite: e.target.checked }));

$("#edit").addEventListener("click", () => chrome.runtime.openOptionsPage());

init();
