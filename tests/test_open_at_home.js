/*
 * The public copy's one job: hand you over to the engine at home.
 *
 * Worth testing rather than eyeballing, because the interesting cases are
 * the ones you would not think to click - a typed address with no scheme,
 * a trailing slash, and above all the card appearing on the copy the engine
 * itself serves, where it would be nonsense ("open Muster" while in Muster).
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

let failures = 0;
function check(name, cond, detail) {
  if (cond) console.log(`  ok   ${name}`);
  else { failures++; console.log(`  FAIL ${name}${detail ? ' - ' + detail : ''}`); }
}

function makeElement() {
  return {
    style: {}, dataset: {}, files: [], value: '', textContent: '', innerHTML: '',
    hidden: false, open: false, href: '', checked: false, offsetParent: {}, scrollHeight: 20,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {}, removeEventListener() {}, setAttribute() {},
    getAttribute: () => null, appendChild() {}, insertBefore() {}, click() {},
    closest: () => null, focus() {}, querySelector: () => makeElement(),
    querySelectorAll: () => [],
  };
}

// The card ships hidden in the markup and is only revealed by the JS, so the
// stub has to start from the real attribute - otherwise "it stayed hidden"
// would pass for the wrong reason. Read it out of index.html rather than
// hard-coding it here, so deleting the attribute breaks this test.
const INDEX_HTML = fs.readFileSync(path.join(__dirname, '..', 'docs', 'index.html'), 'utf8');
const CARD_TAG = (INDEX_HTML.match(/<div class="card" id="open-at-home"[^>]*>/) || [''])[0];
const CARD_STARTS_HIDDEN = /\bhidden\b/.test(CARD_TAG);

function load({ port = '443', protocol = 'https:', saved = null } = {}) {
  const stubs = new Map();
  const alerts = [];
  const $el = sel => {
    if (!stubs.has(sel)) {
      const el = makeElement();
      if (sel === '#open-at-home') el.hidden = CARD_STARTS_HIDDEN;
      stubs.set(sel, el);
    }
    return stubs.get(sel);
  };
  const store = { _d: {} };
  if (saved !== null) store._d['muster.home'] = saved;

  const sandbox = {
    document: {
      documentElement: { dataset: {} },
      querySelector: $el, querySelectorAll: () => [],
      createElement: () => makeElement(), addEventListener() {},
    },
    localStorage: {
      getItem: k => (k in store._d ? store._d[k] : null),
      setItem: (k, v) => { store._d[k] = String(v); },
      removeItem: k => { delete store._d[k]; },
    },
    fetch: async (url) => {
      const u = String(url);
      let body = { ok: true };
      if (u.includes('/postings') || u.includes('/applications') ||
          u.includes('/certs') || u.includes('/sources')) body = [];
      else if (u.includes('/profile/gaps')) body = { complete: 0, total: 14, gaps: [], have: [] };
      else if (u.includes('/stats')) body = { open: 0, deadlines: [], activity: [] };
      else if (u.includes('/email/status')) body = { connected: false };
      return { ok: true, status: 200, headers: { get: () => 'application/json' },
               json: async () => body, text: async () => JSON.stringify(body) };
    },
    console, alert: m => alerts.push(String(m)),
    FileReader: class { readAsDataURL() { this.result = 'data:;base64,QQ=='; this.onload && this.onload(); } },
    setInterval: () => 0, clearInterval: () => {}, setTimeout,
    location: { protocol, port, href: `${protocol}//host/` },
    navigator: { userAgent: 'node' },
    Event: class { constructor(t) { this.type = t; } },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  const code = fs.readFileSync(path.join(__dirname, '..', 'docs', 'app.js'), 'utf8');
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: 'app.js' });
  return { $el, alerts, store, sandbox };
}

const flush = () => new Promise(r => setTimeout(r, 0));

(async () => {
  console.log('open-at-home redirect');

  check('the card ships hidden in the markup', CARD_STARTS_HIDDEN,
    'index.html no longer marks #open-at-home hidden: ' + CARD_TAG);

  // public copy, nothing saved
  {
    const { $el } = load();
    await flush();
    check('the card is shown on the public copy', $el('#open-at-home').hidden === false);
    check('and it points at this computer by default',
      $el('#go-home').href === 'http://127.0.0.1:8770/', $el('#go-home').href);
  }

  // the engine's own copy must NOT offer to open itself
  {
    const { $el } = load({ port: '8770', protocol: 'http:' });
    await flush();
    check('the card stays hidden when the engine served the page',
      $el('#open-at-home').hidden === true);
  }

  // a saved home address wins
  {
    const { $el } = load({ saved: 'http://192.168.1.42:8770' });
    await flush();
    check('a saved home address is used instead',
      $el('#go-home').href === 'http://192.168.1.42:8770/', $el('#go-home').href);
  }

  // typing a bare host:port is accepted and normalised
  {
    const { $el, store, alerts } = load();
    await flush();
    $el('#home-url').value = '192.168.1.42:8770';
    $el('#save-home').onclick();
    check('an address typed without http:// still works',
      store._d['muster.home'] === 'http://192.168.1.42:8770', store._d['muster.home']);
    check('and the button updates immediately',
      $el('#go-home').href === 'http://192.168.1.42:8770/', $el('#go-home').href);
    check('it confirms what it saved', alerts.some(a => a.includes('192.168.1.42')));
  }

  // a trailing slash must not produce a double slash
  {
    const { $el, store } = load();
    await flush();
    $el('#home-url').value = 'http://192.168.1.42:8770/';
    $el('#save-home').onclick();
    check('a trailing slash is not doubled up',
      $el('#go-home').href === 'http://192.168.1.42:8770/', $el('#go-home').href);
    check('and is stripped before storing',
      store._d['muster.home'] === 'http://192.168.1.42:8770', store._d['muster.home']);
  }

  // clearing it goes back to this computer
  {
    const { $el, store } = load({ saved: 'http://192.168.1.42:8770' });
    await flush();
    $el('#home-url').value = '';
    $el('#save-home').onclick();
    check('clearing the field falls back to this computer',
      $el('#go-home').href === 'http://127.0.0.1:8770/', $el('#go-home').href);
    check('and forgets the old address', !('muster.home' in store._d));
  }

  // obvious nonsense is rejected rather than saved
  {
    const { $el, store, alerts } = load();
    await flush();
    $el('#home-url').value = 'http://';
    $el('#save-home').onclick();
    check('an unusable address is refused, not saved',
      !('muster.home' in store._d), JSON.stringify(store._d));
    check('and it says so', alerts.some(a => /does not look like an address/i.test(a)));
  }

  console.log(failures ? `\n${failures} failed` : '\nall passed');
  process.exit(failures ? 1 : 0);
})();
