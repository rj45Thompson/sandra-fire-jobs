/* Apply Assist — field detection.
 *
 * The whole job of this file: look at an <input> and work out which piece of
 * the person's profile belongs in it. It never reads passwords, never touches
 * a form on a login page, and never decides to submit anything.
 *
 * Detection is scored rather than first-match, because job forms label the
 * same field a dozen different ways. Signals, best first:
 *   1. autocomplete=""     — when a site sets it, it is authoritative
 *   2. the visible <label> — what a human reads
 *   3. name / id           — what the developer called it
 *   4. placeholder / aria  — the fallback
 * A field is only filled when the winning score clears MIN_SCORE, so an
 * ambiguous input is left alone instead of being filled with something wrong.
 */

const ApplyFields = (() => {

  const MIN_SCORE = 3;

  /* Profile keys, and how to recognise the input that wants them.
   * `auto` matches the HTML autocomplete token exactly.
   * `strong` are phrases that are decisive on their own.
   * `weak` are hints that only count alongside something else.
   * `not` vetoes the match outright — this is what keeps "first name" out of
   * "company name" and keeps us away from anything password-shaped. */
  const RULES = [
    { key: "firstName", auto: ["given-name"],
      strong: [/\bfirst\s*name\b/, /\bgiven\s*name\b/, /\bforename\b/],
      weak: [/\bfname\b/, /\bfirst\b/],
      not: [/last/, /company/, /school/, /employer/, /referen/] },

    { key: "lastName", auto: ["family-name"],
      strong: [/\blast\s*name\b/, /\bfamily\s*name\b/, /\bsurname\b/],
      weak: [/\blname\b/, /\blast\b/],
      not: [/first/, /company/, /school/, /employer/, /referen/] },

    { key: "fullName", auto: ["name"],
      strong: [/\bfull\s*name\b/, /\byour\s*name\b/, /^name$/],
      weak: [/\bname\b/],
      not: [/first/, /last/, /given/, /family/, /user/, /company/, /school/,
            /employer/, /file/, /referen/, /contact\s*name/] },

    { key: "email", auto: ["email"],
      strong: [/\be[\s-]?mail\b/],
      weak: [],
      not: [/confirm/, /referen/] },

    { key: "phone", auto: ["tel", "tel-national"],
      strong: [/\bphone\b/, /\bmobile\b/, /\btelephone\b/, /\bcell\b/],
      weak: [/\btel\b/],
      not: [/country\s*code/, /extension/, /referen/] },

    { key: "address", auto: ["street-address", "address-line1"],
      strong: [/\bstreet\s*address\b/, /\baddress\s*line\s*1\b/, /^address$/],
      weak: [/\baddress\b/],
      not: [/email/, /line\s*2/, /city/, /state/, /zip/, /postal/, /country/] },

    { key: "city", auto: ["address-level2"],
      strong: [/\bcity\b/, /\btown\b/],
      weak: [/\blocality\b/],
      not: [/state/, /country/] },

    { key: "region", auto: ["address-level1"],
      strong: [/\bprovince\b/, /\bstate\b/, /\bregion\b/],
      weak: [],
      not: [/country/, /united\s*states/] },

    { key: "postal", auto: ["postal-code"],
      strong: [/\bpostal\s*code\b/, /\bzip\s*code\b/, /\bpostcode\b/],
      weak: [/\bzip\b/],
      not: [] },

    { key: "country", auto: ["country", "country-name"],
      strong: [/\bcountry\b/],
      weak: [],
      not: [/code/] },

    { key: "linkedin", auto: [],
      strong: [/\blinked\s*in\b/],
      weak: [],
      not: [] },

    { key: "github", auto: [],
      strong: [/\bgit\s*hub\b/],
      weak: [],
      not: [] },

    { key: "website", auto: ["url"],
      strong: [/\bportfolio\b/, /\bpersonal\s*(web)?site\b/, /\bwebsite\b/, /\bweb\s*page\b/],
      weak: [/\burl\b/],
      not: [/linked/, /git/, /company/] },

    { key: "currentTitle", auto: ["organization-title"],
      strong: [/\bcurrent\s*(job\s*)?title\b/, /\bjob\s*title\b/, /\bposition\s*title\b/],
      weak: [/\btitle\b/],
      not: [/mr|mrs|ms\b/, /job\s*you/, /applying/] },

    { key: "currentCompany", auto: ["organization"],
      strong: [/\bcurrent\s*(employer|company)\b/, /\bemployer\b/, /\bcompany\s*name\b/],
      weak: [/\bcompany\b/],
      not: [/why|reason/, /our\s*company/] },

    { key: "yearsExperience", auto: [],
      strong: [/\byears?\s*of\s*(relevant\s*)?experience\b/, /\bhow\s*many\s*years\b/],
      weak: [/\bexperience\b/, /\byears\b/],
      not: [] },

    { key: "salary", auto: [],
      strong: [/\bsalary\s*(expectation|requirement)/, /\bexpected\s*salary\b/,
               /\bdesired\s*(salary|compensation)\b/, /\bcompensation\s*expectation/],
      weak: [/\bsalary\b/, /\bcompensation\b/, /\brate\b/],
      not: [/current\s*salary/] },

    { key: "availability", auto: [],
      strong: [/\bnotice\s*period\b/, /\bstart\s*date\b/, /\bwhen\s*can\s*you\s*start\b/,
               /\bavailability\b/, /\bavailable\s*to\s*start\b/],
      weak: [/\bavailable\b/],
      not: [] },

    { key: "coverLetter", auto: [],
      strong: [/\bcover\s*letter\b/, /\bwhy\s*do\s*you\s*want\b/, /\bwhy\s*are\s*you\s*interested\b/,
               /\btell\s*us\s*about\s*yourself\b/, /\bwhy\s*should\s*we\b/,
               /\badditional\s*information\b/, /\banything\s*else\b/],
      weak: [/\bmessage\b/, /\bnote\b/],
      not: [] },

    { key: "referral", auto: [],
      strong: [/\bhow\s*did\s*you\s*hear\b/, /\bwhere\s*did\s*you\s*(hear|find)\b/,
               /\breferr?al\s*source\b/, /\bsource\b/],
      weak: [],
      not: [] },

    { key: "workAuth", auto: [],
      strong: [/\bauthoriz(ed|ation)\s*to\s*work\b/, /\blegally\s*(authorized|entitled)\b/,
               /\bwork\s*authorization\b/, /\bright\s*to\s*work\b/],
      weak: [],
      not: [/sponsor/] },

    { key: "sponsorship", auto: [],
      strong: [/\brequire\s*sponsorship\b/, /\bneed\s*sponsorship\b/,
               /\bvisa\s*sponsorship\b/, /\bsponsorship\b/],
      weak: [],
      not: [] }
  ];

  /* File uploads are classified separately from text fields: they are filled by
   * attaching bytes, not by typing, so they take a different code path in
   * content.js. Distinguishing resume from cover letter matters — attaching the
   * wrong document to an application is worse than attaching nothing. */
  const FILE_RULES = [
    { key: "resumeFile",
      strong: [/\bresume\b/, /\bresum\u00e9\b/, /\bcv\b/, /curriculum\s*vitae/],
      weak: [],
      not: [/cover/, /letter/, /transcript/, /portfolio/, /photo/, /certificat/] },

    { key: "coverLetterFile",
      strong: [/\bcover\s*letter\b/, /\bcovering\s*letter\b/],
      weak: [],
      not: [/resume/, /\bcv\b/] }
  ];

  /* Fields we deliberately never touch, even if asked.
   * EEO/demographic answers are the applicant's own to give, and anything
   * password- or payment-shaped has no business being autofilled. */
  const NEVER = [
    /password/, /passcode/, /\bpin\b/, /security\s*question/,
    /credit\s*card/, /\bcvv\b/, /card\s*number/, /account\s*number/,
    /\bsin\b/, /social\s*insurance/, /social\s*security/, /\bssn\b/,
    /\bgender\b/, /\brace\b/, /\bethnic/, /\bveteran\b/, /\bdisabilit/,
    /sexual\s*orientation/, /date\s*of\s*birth/, /\bdob\b/
  ];

  /* Underscores and hyphens separate words in `name` and `id` attributes but
   * count as word characters to a regex, so `\blast\s*name\b` misses
   * `last_name` and `\bphone\b` misses `phone_number`. Flatten them first. */
  const norm = (s) => (s || "")
    .toLowerCase()
    .replace(/[_\-]+/g, " ")
    .replace(/[\s\u00a0]+/g, " ")
    .trim();

  /** Everything the page tells us about this input, as one lowercase string. */
  function signature(el) {
    const bits = [];

    // The visible label — for="", a wrapping <label>, or aria.
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) bits.push(l.textContent);
    }
    const wrapping = el.closest("label");
    if (wrapping) bits.push(wrapping.textContent);

    if (el.getAttribute("aria-label")) bits.push(el.getAttribute("aria-label"));
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      labelledBy.split(/\s+/).forEach((id) => {
        const n = document.getElementById(id);
        if (n) bits.push(n.textContent);
      });
    }

    // Workday and friends wrap each question in a group with its own heading.
    const group = el.closest("[data-automation-id], fieldset, .field, .form-group, li");
    if (group) {
      const heading = group.querySelector("legend, label, .label, [class*='label']");
      if (heading && !heading.contains(el)) bits.push(heading.textContent);
    }

    bits.push(el.name, el.id, el.placeholder, el.getAttribute("data-automation-id"));
    return norm(bits.filter(Boolean).join(" ")).slice(0, 400);
  }

  function isFillable(el) {
    if (!el || el.disabled || el.readOnly) return false;
    if (el.type === "hidden" || el.type === "password" || el.type === "submit" ||
        el.type === "button" || el.type === "file") return false;
    if (el.offsetParent === null && el.type !== "select-one") return false;   // not visible
    return true;
  }

  /** Score one rule against a signature. Returns 0 when it does not apply. */
  function score(rule, sig, autoAttr) {
    if (rule.not && rule.not.some((r) => r.test(sig))) return 0;
    let s = 0;
    if (autoAttr && rule.auto.includes(autoAttr)) s += 5;
    if (rule.strong.some((r) => r.test(sig))) s += 4;
    if (rule.weak.some((r) => r.test(sig))) s += 2;
    return s;
  }

  /** Which profile key belongs in this element, or null to leave it alone. */
  function classify(el) {
    if (!isFillable(el)) return null;
    const sig = signature(el);
    if (!sig) return null;
    if (NEVER.some((r) => r.test(sig))) return null;

    /* NOT norm() — autocomplete is a controlled vocabulary whose tokens
     * contain hyphens ("given-name"), so flattening them would turn every
     * one of them into a bare "name". Split on whitespace only, and take
     * the last token so "shipping given-name" still resolves. */
    const autoAttr = (el.getAttribute("autocomplete") || "")
      .toLowerCase().trim().split(/\s+/).pop();

    let best = null, bestScore = 0;
    for (const rule of RULES) {
      const s = score(rule, sig, autoAttr);
      if (s > bestScore) { bestScore = s; best = rule.key; }
    }
    return bestScore >= MIN_SCORE ? best : null;
  }

  /** Which document belongs in this file input, or null to leave it alone.
   *  Separate from classify() because file inputs are filled by attaching bytes
   *  rather than by typing — see attachFile() in content.js. */
  function classifyFile(el) {
    if (!el || el.type !== "file" || el.disabled) return null;
    const sig = signature(el);
    if (!sig) return null;
    if (NEVER.some((r) => r.test(sig))) return null;

    let best = null, bestScore = 0;
    for (const rule of FILE_RULES) {
      const s = score(rule, sig, null);
      if (s > bestScore) { bestScore = s; best = rule.key; }
    }
    return bestScore >= MIN_SCORE ? best : null;
  }

  /** Every candidate field on the page, already classified.
   *  Entries carry `kind`: "text" is typed, "file" is attached. */
  function scan(root = document) {
    const out = [];
    root.querySelectorAll("input, textarea, select").forEach((el) => {
      if (el.type === "file") {
        const key = classifyFile(el);
        if (key) out.push({ el, key, kind: "file" });
        return;
      }
      const key = classify(el);
      if (key) out.push({ el, key, kind: "text" });
    });
    return out;
  }

  return { classify, classifyFile, scan, signature, RULES, FILE_RULES, NEVER, MIN_SCORE };
})();

if (typeof module !== "undefined") module.exports = ApplyFields;
