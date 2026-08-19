/*
 * Front-end upload behaviour, run in plain node - no jsdom, no npm install,
 * same stdlib-only rule the rest of this project follows.
 *
 * The bug this exists for: the résumé upload handler only refreshed the
 * "Still needed" list when the server reported a lifted CERTIFICATION.
 * A résumé that yielded profile details but no recognised certificate left
 * the list stale, which is precisely the case the merged Profile & documents
 * section is built around. It looked like the upload had done nothing.
 *
 * app.js expects a browser, so it gets a permissive fake DOM: querySelector
 * hands back the same stub for the same selector every time, which is what
 * lets the test grab a real handler off a real element and fire it.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

let failures = 0;
function check(name, cond, detail) {
  if (cond) { console.log(`  ok   ${name}`); }
  else { failures++; console.log(`  FAIL ${name}${detail ? ' - ' + detail : ''}`); }
}

function makeElement() {
  const el = {
    style: {}, dataset: {}, files: [], value: '', textContent: '', innerHTML: '',
    hidden: false, checked: false, offsetParent: {}, scrollHeight: 20,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {}, removeEventListener() {}, setAttribute() {},
    getAttribute: () => null, appendChild() {}, insertBefore() {}, click() {},
    closest: () => null, focus() {}, scrollTo() {},
    querySelector: () => makeElement(), querySelectorAll: () => [],
  };
  return el;
}

function loadApp({ uploadResponse }) {
  const stubs = new Map();
  const calls = { fetched: [], alerts: [] };

  const $el = sel => {
    if (!stubs.has(sel)) stubs.set(sel, makeElement());
    return stubs.get(sel);
  };

  const document = {
    documentElement: { dataset: {} },
    querySelector: $el,
    querySelectorAll: () => [],
    createElement: () => makeElement(),
    addEventListener() {},
  };

  const localStorage = {
    _d: {},
    getItem(k) { return k in this._d ? this._d[k] : null; },
    setItem(k, v) { this._d[k] = String(v); },
  };

  async function fetchMock(url, opts = {}) {
    calls.fetched.push(String(url));
    let body = { ok: true };
    if (String(url).includes('/health')) body = { ok: true, version: '0.1.0' };
    else if (String(url).includes('/upload')) body = uploadResponse;
    else if (String(url).includes('/profile/gaps')) body = { complete: 3, total: 14, gaps: [], have: [] };
    else if (String(url).includes('/certs')) body = [];
    else if (String(url).includes('/sources')) body = [];
    else if (String(url).includes('/postings')) body = [];
    else if (String(url).includes('/applications')) body = [];
    else if (String(url).includes('/stats')) body = { open: 0, sent: 0, deadlines: [], activity: [] };
    else if (String(url).includes('/email/status')) body = { connected: false };
    return {
      ok: true, status: 200,
      headers: { get: () => 'application/json' },
      json: async () => body,
      text: async () => JSON.stringify(body),
    };
  }

  class FileReaderStub {
    readAsDataURL() { this.result = 'data:application/octet-stream;base64,QUJD'; this.onload && this.onload(); }
  }

  const sandbox = {
    document, localStorage, fetch: fetchMock, console,
    FileReader: FileReaderStub,
    alert: m => calls.alerts.push(String(m)),
    setInterval: () => 0, clearInterval: () => {}, setTimeout,
    location: { protocol: 'http:', port: '8770', href: 'http://127.0.0.1:8770/' },
    navigator: { userAgent: 'node' },
    Event: class { constructor(t) { this.type = t; } },
    DataTransfer: class { constructor() { this.files = []; } },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  const code = fs.readFileSync(path.join(__dirname, '..', 'docs', 'app.js'), 'utf8');
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: 'app.js' });

  return { stubs, calls, sandbox, $el };
}

const flush = () => new Promise(r => setTimeout(r, 0));

(async () => {
  console.log('front-end upload behaviour');

  // ---- the regression: profile details but NO certification ----
  {
    const { $el, calls } = loadApp({
      uploadResponse: { ok: true, lifted_certs: 0, lifted_fields: 3 },
    });
    await flush(); await flush();

    // Count BEFORE the upload: boot() already loads the gaps once, so a
    // bare "did it ever fetch /profile/gaps" assertion passes even when the
    // upload refreshes nothing. Only the delta says anything.
    const before = calls.fetched.filter(u => u.includes('/profile/gaps')).length;

    const input = $el('#file-resume');
    input.files = [{ name: 'resume.docx', size: 1200 }];
    await input.onchange();
    await flush(); await flush();

    const after = calls.fetched.filter(u => u.includes('/profile/gaps')).length;
    check('a résumé with no certifications still refreshes the needs list',
      after > before, `gaps requests went ${before} -> ${after}`);

    check('it tells her what it read',
      calls.alerts.some(a => a.includes('3 profile detail')),
      JSON.stringify(calls.alerts));
  }

  // ---- the case that always worked, still works ----
  {
    const { $el, calls } = loadApp({
      uploadResponse: { ok: true, lifted_certs: 2, lifted_fields: 5 },
    });
    await flush(); await flush();

    const beforeGaps = calls.fetched.filter(u => u.includes('/profile/gaps')).length;
    const beforeCerts = calls.fetched.filter(u => u.includes('/certs')).length;

    const input = $el('#file-resume');
    input.files = [{ name: 'resume.docx', size: 1200 }];
    await input.onchange();
    await flush(); await flush();

    check('a résumé with certifications refreshes both lists',
      calls.fetched.filter(u => u.includes('/profile/gaps')).length > beforeGaps &&
      calls.fetched.filter(u => u.includes('/certs')).length > beforeCerts);

    check('the summary names both counts',
      calls.alerts.some(a => a.includes('2 certification') && a.includes('5 profile detail')),
      JSON.stringify(calls.alerts));
  }

  // ---- a résumé nothing could be read from ----
  {
    const { $el, calls } = loadApp({
      uploadResponse: { ok: true, lifted_certs: 0, lifted_fields: 0 },
    });
    await flush(); await flush();

    const input = $el('#file-resume');
    input.files = [{ name: 'scan.docx', size: 900 }];
    await input.onchange();
    await flush(); await flush();

    check('it says plainly that nothing could be read, instead of a bare 0',
      calls.alerts.some(a => /could not read/i.test(a)),
      JSON.stringify(calls.alerts));

    check('and it still points her at the chat to fill the rest in',
      calls.alerts.some(a => /chat/i.test(a)),
      JSON.stringify(calls.alerts));
  }

  // ---- a supporting document is not a résumé ----
  {
    const { $el, calls } = loadApp({
      uploadResponse: { ok: true, lifted_certs: 0, lifted_fields: 0 },
    });
    await flush(); await flush();

    const before = calls.fetched.filter(u => u.includes('/profile/gaps')).length;
    const input = $el('#file-doc');
    input.files = [{ name: 'abstract.pdf', size: 4000 }];
    await input.onchange();
    await flush(); await flush();

    const after = calls.fetched.filter(u => u.includes('/profile/gaps')).length;
    check('uploading a supporting document does not re-read the profile',
      after === before, `gaps requests went ${before} -> ${after}`);
  }

  console.log(failures ? `\n${failures} failed` : '\nall passed');
  process.exit(failures ? 1 : 0);
})();
