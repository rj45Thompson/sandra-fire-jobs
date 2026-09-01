/* Apply Assist — service worker.
 *
 * Deliberately thin. The profile lives in chrome.storage.local, which means it
 * stays on this machine and is never sent anywhere by this extension. There is
 * no account, no sync, and no backend call in the default build — if you later
 * add a "write my cover letter" endpoint, it goes here and nowhere else, so
 * there is exactly one place to audit what leaves the browser.
 */

const DEFAULT_PROFILE = {
  firstName: "", lastName: "", fullName: "",
  email: "", phone: "",
  address: "", city: "", region: "", postal: "", country: "",
  linkedin: "", github: "", website: "",
  currentTitle: "", currentCompany: "", yearsExperience: "",
  salary: "", availability: "",
  workAuth: "", sponsorship: "",
  referral: "", coverLetter: ""
};

chrome.runtime.onInstalled.addListener(async ({ reason }) => {
  const { profile } = await chrome.storage.local.get("profile");
  if (!profile) {
    await chrome.storage.local.set({ profile: DEFAULT_PROFILE, overwrite: false });
  }
  if (reason === "install") {
    chrome.runtime.openOptionsPage();
  }
});

/* The content script is declared for the known ATS hosts. On anything else the
 * person can still ask for a fill from the popup, which injects on demand using
 * activeTab — a permission granted by their click and nothing wider. */
chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
  if (msg.type === "AA_INJECT") {
    chrome.scripting.executeScript({
      target: { tabId: msg.tabId, allFrames: true },
      files: ["src/fields.js", "src/content.js"]
    })
      .then(() => chrome.scripting.insertCSS({
        target: { tabId: msg.tabId, allFrames: true },
        files: ["src/overlay.css"]
      }))
      .then(() => respond({ ok: true }))
      .catch((e) => respond({ ok: false, error: String(e.message || e) }));
    return true;
  }
});
