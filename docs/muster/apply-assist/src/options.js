/* Apply Assist — profile editor.
 * The form is generated from this spec so that adding a field means adding one
 * line here and one rule in fields.js, rather than editing HTML by hand. */

const SPEC = [
  { legend: "You", fields: [
    { k: "firstName", l: "First name" },
    { k: "lastName",  l: "Last name" },
    { k: "fullName",  l: "Full name", hint: "Only if it differs from first + last" },
    { k: "email",     l: "Email", type: "email" },
    { k: "phone",     l: "Phone", type: "tel" }
  ]},
  { legend: "Where you are", fields: [
    { k: "address", l: "Street address" },
    { k: "city",    l: "City" },
    { k: "region",  l: "Province or state" },
    { k: "postal",  l: "Postal code" },
    { k: "country", l: "Country" }
  ]},
  { legend: "Links", fields: [
    { k: "linkedin", l: "LinkedIn", type: "url" },
    { k: "github",   l: "GitHub", type: "url" },
    { k: "website",  l: "Portfolio or site", type: "url" }
  ]},
  { legend: "Work", fields: [
    { k: "currentTitle",    l: "Current title" },
    { k: "currentCompany",  l: "Current company" },
    { k: "yearsExperience", l: "Years of experience" },
    { k: "salary",          l: "Salary expectation",
      hint: "\"Negotiable\" is a valid answer and often the better one" },
    { k: "availability",    l: "Availability / notice",
      hint: "e.g. \"Available immediately, no notice period\"" }
  ]},
  { legend: "Standard questions", fields: [
    { k: "workAuth", l: "Authorised to work?", type: "select",
      options: ["", "Yes", "No"] },
    { k: "sponsorship", l: "Need sponsorship?", type: "select",
      options: ["", "No", "Yes"] },
    { k: "referral", l: "How did you hear about us?",
      hint: "A sensible default for the question every form asks" }
  ]},
  { legend: "Long answers", fields: [
    { k: "coverLetter", l: "Cover letter / \"why this role\"", type: "textarea", full: true,
      hint: "A general version. Worth tailoring per application before you submit." }
  ]}
];

const form = document.getElementById("form");

for (const group of SPEC) {
  const fs = document.createElement("fieldset");
  const lg = document.createElement("legend");
  lg.textContent = group.legend;
  fs.append(lg);

  const grid = document.createElement("div");
  grid.className = "grid";

  for (const f of group.fields) {
    const cell = document.createElement("div");
    if (f.full || f.type === "textarea") cell.className = "full";

    const label = document.createElement("label");
    label.textContent = f.l;
    label.htmlFor = f.k;

    let input;
    if (f.type === "textarea") {
      input = document.createElement("textarea");
    } else if (f.type === "select") {
      input = document.createElement("select");
      for (const o of f.options) {
        const opt = document.createElement("option");
        opt.value = o;
        opt.textContent = o || "—";
        input.append(opt);
      }
    } else {
      input = document.createElement("input");
      input.type = f.type || "text";
    }
    input.id = f.k;
    input.name = f.k;

    cell.append(label, input);
    if (f.hint) {
      const h = document.createElement("p");
      h.className = "hint";
      h.textContent = f.hint;
      cell.append(h);
    }
    grid.append(cell);
  }
  fs.append(grid);
  form.append(fs);
}

const keys = SPEC.flatMap((g) => g.fields.map((f) => f.k));

/* Files are stored as base64 in chrome.storage.local so the content script has
 * actual bytes to hand to a DataTransfer. A path would be useless — the browser
 * will not let a page read one, which is why "attach my resume" needs the file
 * itself rather than a reference to it.
 * TASKS #4/#5 move this to the web app; until then it stays on this machine. */
const FILE_KEYS = ["resumeFile", "coverLetterFile"];
const STATE_EL = { resumeFile: "resumeState", coverLetterFile: "coverState" };
const MAX_FILE = 5 * 1024 * 1024;

function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onerror = () => reject(new Error("Could not read that file."));
    r.onload = () => {
      const s = String(r.result);
      resolve(s.slice(s.indexOf(",") + 1));      // strip the data: prefix
    };
    r.readAsDataURL(file);
  });
}

function showFileState(key, spec) {
  const el = document.getElementById(STATE_EL[key]);
  if (!el) return;
  el.textContent = spec && spec.name
    ? `Saved: ${spec.name} (${Math.round((spec.size || 0) / 1024)} KB)`
    : (key === "resumeFile" ? "No resume saved." : "No cover letter saved.");
}

for (const key of FILE_KEYS) {
  const input = document.getElementById(key);
  if (!input) continue;
  input.addEventListener("change", async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    if (file.size > MAX_FILE) {
      document.getElementById(STATE_EL[key]).textContent =
        "That file is over 5 MB — most portals reject those anyway.";
      input.value = "";
      return;
    }
    try {
      const data = await readAsBase64(file);
      const spec = { name: file.name, mimeType: file.type || "application/pdf",
                     size: file.size, data };
      const { profile } = await chrome.storage.local.get("profile");
      const next = Object.assign({}, profile || {});
      next[key] = spec;
      await chrome.storage.local.set({ profile: next });
      showFileState(key, spec);
      flash();
    } catch (e) {
      document.getElementById(STATE_EL[key]).textContent = String(e.message || e);
    }
  });
}

async function load() {
  const { profile } = await chrome.storage.local.get("profile");
  if (!profile) return;
  for (const k of keys) {
    const el = document.getElementById(k);
    if (el && profile[k] !== undefined) el.value = profile[k];
  }
  for (const key of FILE_KEYS) showFileState(key, profile[key]);
}

function flash() {
  const s = document.getElementById("saved");
  s.classList.add("on");
  setTimeout(() => s.classList.remove("on"), 1400);
}

document.getElementById("save").addEventListener("click", async () => {
  const { profile: existing } = await chrome.storage.local.get("profile");
  const profile = Object.assign({}, existing || {});   // keep saved files
  for (const k of keys) profile[k] = document.getElementById(k).value.trim();
  await chrome.storage.local.set({ profile });
  flash();
});

document.getElementById("clear").addEventListener("click", async () => {
  if (!confirm("Clear every saved detail? This cannot be undone.")) return;
  const empty = {};
  for (const k of keys) empty[k] = "";
  await chrome.storage.local.set({ profile: empty });     // drops saved files too
  for (const k of keys) document.getElementById(k).value = "";
  for (const key of FILE_KEYS) {
    const input = document.getElementById(key);
    if (input) input.value = "";
    showFileState(key, null);
  }
  flash();
});

load();
