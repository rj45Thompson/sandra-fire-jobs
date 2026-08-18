/* ===============================================================
   Muster - front-end
   Talks to the local engine over HTTP. Degrades to a local-only
   demo when the engine is not running, so the page is never dead.
   =============================================================== */

/* When the engine serves this page, talk to it with relative URLs: same
   origin, no CORS, no token, nothing for a browser or extension to block.
   The stored URL is only used when the page is opened from somewhere else,
   such as the public GitHub Pages copy. */
const SELF_HOSTED = /^https?:$/.test(location.protocol) &&
                    location.port === '8770';

const CFG = {
  api: SELF_HOSTED ? '' : (localStorage.getItem('muster.api') || 'http://127.0.0.1:8770'),
  token: localStorage.getItem('muster.token') || '',
};

let ONLINE = false;

/* ---------- tiny helpers ---------- */
const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, opts = {}) {
  const r = await fetch(CFG.api + path, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...(CFG.token ? { 'X-Muster-Token': CFG.token } : {}),
      ...(opts.headers || {}),
    },
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => '')}`.slice(0, 160));
  const ct = r.headers.get('content-type') || '';
  return ct.includes('json') ? r.json() : r.text();
}

/* ---------- local fallback store ---------- */
const LS = {
  get: (k, d) => { try { return JSON.parse(localStorage.getItem('muster.' + k)) ?? d; } catch { return d; } },
  set: (k, v) => localStorage.setItem('muster.' + k, JSON.stringify(v)),
};

/* ---------- navigation ---------- */
const TITLES = {
  dash:   ['Dashboard', 'Your search at a glance'],
  profile: ['Profile', 'Everything the applications ask for'],
  docs:   ['Documents', 'Résumé, certifications and expiry dates'],
  jobs:   ['Jobs', 'Every employer, watched'],
  apps:   ['Applications', 'Submitted, in review, and replies'],
  chat:   ['Chat', 'Ask anything about the search'],
  upgrade: ['Upgrade me', 'Change how this app looks and reads'],
};

$('#nav').addEventListener('click', e => {
  const b = e.target.closest('button[data-panel]');
  if (!b) return;
  show(b.dataset.panel);
});

function show(id) {
  $$('#nav button').forEach(x => x.setAttribute('aria-current', String(x.dataset.panel === id)));
  $$('.panel').forEach(p => p.hidden = p.id !== 'p-' + id);
  const [t, s] = TITLES[id] || ['Muster', ''];
  $('#page-title').textContent = t;
  $('#page-sub').textContent = s;
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* ---------- theme ---------- */
$('#btn-theme').onclick = () => {
  const cur = document.documentElement.dataset.theme;
  const next = cur === 'dark' ? 'light' : cur === 'light' ? '' : 'dark';
  if (next) document.documentElement.dataset.theme = next;
  else delete document.documentElement.dataset.theme;
  LS.set('theme', next);
};
{
  const t = LS.get('theme', '');
  if (t) document.documentElement.dataset.theme = t;
}

/* ---------- connection ---------- */
async function ping() {
  try {
    const h = await api('/health');
    setConn(true, h.version ? `Engine v${h.version}` : 'Connected');
    return true;
  } catch {
    setConn(false, 'Engine offline');
    return false;
  }
}

function setConn(ok, text) {
  ONLINE = ok;
  $('#conn-dot').classList.toggle('live', ok);
  $('#conn-text').textContent = text;
  $('#btn-connect').textContent = ok ? 'Settings' : 'Connect';
}

$('#btn-connect').onclick = async () => {
  if (SELF_HOSTED) {
    // Nothing to configure - the engine served this page.
    setConn(ONLINE, ONLINE ? 'Engine v0.1.0' : 'Engine offline');
    await ping();
    if (ONLINE) refreshAll();
    return;
  }
  let url, tok;
  try {
    url = window.prompt('Local engine URL', CFG.api || 'http://127.0.0.1:8770');
    if (url === null) return;
    tok = window.prompt('API token (from your .env file)', CFG.token);
    if (tok === null) return;
  } catch {
    // Some browsers suppress prompt(); fall back to the known default.
    url = 'http://127.0.0.1:8770';
    tok = CFG.token;
    alert('This browser blocks pop-up prompts.\n\n' +
          'Open the app directly at http://127.0.0.1:8770 instead - ' +
          'no token is needed there.');
  }
  CFG.api = String(url).replace(/\/+$/, '');
  CFG.token = String(tok || '');
  localStorage.setItem('muster.api', CFG.api);
  localStorage.setItem('muster.token', CFG.token);
  if (await ping()) refreshAll();
};

/* ---------- profile ---------- */
const PFORM = $('#profile-form');

PFORM.addEventListener('submit', async e => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(PFORM).entries());
  LS.set('profile', data);
  let msg = 'Saved locally';
  if (ONLINE) {
    try { await api('/profile', { method: 'POST', body: JSON.stringify(data) }); msg = 'Saved to engine'; }
    catch (err) { msg = 'Saved locally (engine error)'; }
  }
  $('#profile-saved').textContent = msg + ' ✓';
  setTimeout(() => $('#profile-saved').textContent = '', 3200);
  updateProfileTag();
});

function loadProfile() {
  const p = LS.get('profile', {});
  Object.entries(p).forEach(([k, v]) => {
    const el = PFORM.elements[k];
    if (el) el.value = v;
  });
  updateProfileTag();
}

function updateProfileTag() {
  const fields = [...PFORM.elements].filter(e => e.name);
  const filled = fields.filter(e => String(e.value || '').trim()).length;
  const pct = fields.length ? Math.round(filled / fields.length * 100) : 0;
  $('#tag-profile').textContent = pct + '%';
}
PFORM.addEventListener('input', updateProfileTag);

/* ---------- certifications ---------- */
async function loadCerts() {
  if (ONLINE) { try { LS.set('certs', await api('/certs')); } catch {} }
  renderCerts();
}

function renderCerts() {
  const certs = LS.get('certs', []);
  const box = $('#cert-list');
  $('#tag-docs').textContent = certs.length;
  if (!box) return;
  if (!certs.length) {
    box.innerHTML = '<div class="empty"><div class="big">❋</div>Upload your résumé and your certifications appear here.</div>';
    return;
  }
  const now = Date.now(), DAY = 864e5;
  box.innerHTML = certs.map((c, i) => {
    let chip = '<span class="chip">no expiry</span>';
    if (c.expiry) {
      const days = Math.round((new Date(c.expiry) - now) / DAY);
      chip = days < 0      ? `<span class="chip bad">expired ${-days}d ago</span>`
           : days < 90     ? `<span class="chip warn">expires in ${days}d</span>`
           :                 `<span class="chip ok">valid ${days}d</span>`;
    }
    const st = c.status === 'Complete' ? 'ok' : c.status === 'Expired' ? 'bad' : 'warn';
    const from = c.source === 'resume' ? '<span class="chip pink">from résumé</span>' : '';
    return `<div class="dl">
      <div class="what">
        <b>${esc(c.name)}</b>
        <span><span class="chip ${st}">${esc(c.status)}</span> ${chip} ${from}</span>
      </div>
      <button class="btn quiet" style="padding:6px 13px;font-size:12.5px" data-del="${c.id ?? i}">Remove</button>
    </div>`;
  }).join('');
  $$('[data-del]', box).forEach(b => b.onclick = async () => {
    if (ONLINE) { try { await api('/certs/delete', { method: 'POST', body: JSON.stringify({ id: +b.dataset.del }) }); } catch {} }
    await loadCerts();
  });
}

$('#btn-add-cert').onclick = async () => {
  const c = {
    name: $('#cert-name').value,
    status: $('#cert-status').value,
    expiry: $('#cert-expiry').value || null,
  };
  const list = LS.get('certs', []); list.push(c); LS.set('certs', list);
  $('#cert-expiry').value = '';
  renderCerts();
  if (ONLINE) { try { await api('/certs', { method: 'POST', body: JSON.stringify(c) }); } catch {} }
};

/* ---------- uploads ---------- */
function wireDrop(zoneSel, inputSel, kind, listSel) {
  const zone = $(zoneSel), input = $(inputSel);
  zone.onclick = () => input.click();
  zone.ondragover = e => { e.preventDefault(); zone.classList.add('over'); };
  zone.ondragleave = () => zone.classList.remove('over');
  zone.ondrop = e => {
    e.preventDefault(); zone.classList.remove('over');
    handle([...e.dataTransfer.files]);
  };
  input.onchange = () => handle([...input.files]);

  async function handle(files) {
    if (!files.length) return;
    const list = LS.get(kind, []);
    for (const f of files) {
      list.push({ name: f.name, size: f.size, at: new Date().toISOString(), uploaded: false });
      if (ONLINE) {
        try {
          const b64 = await toB64(f);
          const res = await api('/upload', {
            method: 'POST',
            body: JSON.stringify({ kind, filename: f.name, content_b64: b64 }),
          });
          list[list.length - 1].uploaded = true;
          if (kind === 'resumes' && res.lifted_certs) {
            await loadCerts();
            alert(`Read your résumé and added ${res.lifted_certs} certification${res.lifted_certs === 1 ? '' : 's'}. Check the Certifications list.`);
          }
        } catch (err) { console.warn('upload failed', err); }
      }
    }
    LS.set(kind, list);
    renderFiles(listSel, kind);
  }
}

const toB64 = f => new Promise((res, rej) => {
  const r = new FileReader();
  r.onload = () => res(String(r.result).split(',')[1]);
  r.onerror = rej;
  r.readAsDataURL(f);
});

function renderFiles(sel, kind) {
  const list = LS.get(kind, []);
  const box = $(sel);
  if (!list.length) { box.innerHTML = ''; return; }
  box.innerHTML = list.map((f, i) => `<div class="dl">
    <div class="what"><b>${esc(f.name)}</b>
      <span>${(f.size / 1024).toFixed(0)} KB ·
      ${f.uploaded ? '<span class="chip ok">on engine</span>' : '<span class="chip warn">local only</span>'}</span>
    </div>
    <button class="btn quiet" style="padding:6px 13px;font-size:12.5px" data-rm="${i}">Remove</button>
  </div>`).join('');
  $$('[data-rm]', box).forEach(b => b.onclick = () => {
    const l = LS.get(kind, []); l.splice(+b.dataset.rm, 1); LS.set(kind, l); renderFiles(sel, kind);
  });
}

wireDrop('#drop-resume', '#file-resume', 'resumes', '#resume-list');
wireDrop('#drop-doc', '#file-doc', 'documents', '#doc-list');

/* ---------- jobs ---------- */
let JOBS = [];

async function loadJobs() {
  if (!ONLINE) return;
  try {
    JOBS = await api('/postings');
    renderJobs();
  } catch (e) { console.warn(e); }
}

function renderJobs() {
  const q = $('#job-filter').value.toLowerCase();
  const rows = JOBS.filter(j =>
    !q || (j.employer + ' ' + j.title + ' ' + (j.city || '')).toLowerCase().includes(q));
  const body = $('#jobs-body');
  $('#tag-jobs').textContent = JOBS.length;

  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="8"><div class="empty"><div class="big">◎</div>
      ${JOBS.length ? 'Nothing matches that filter.' : 'No postings yet.<br><small>Connect the engine, then load employers and scan.</small>'}
      </div></td></tr>`;
    return;
  }
  body.innerHTML = rows.map(j => {
    const m = j.match ?? 0;
    const chip = m >= 75 ? 'ok' : m >= 50 ? 'warn' : '';
    return `<tr>
      <td><b>${esc(j.employer)}</b></td>
      <td class="wrap-cell">${esc(j.title)}</td>
      <td>${esc(j.employment_type || '—')}</td>
      <td>${esc(j.city || '—')}</td>
      <td>${esc(j.closes || 'open')}</td>
      <td><span class="chip ${chip}">${m}%</span></td>
      <td><span class="chip">${esc(j.ats || '?')}</span></td>
      <td><a class="btn ghost" style="padding:5px 13px;font-size:12.5px" href="${esc(j.url)}" target="_blank" rel="noopener">Open</a></td>
    </tr>`;
  }).join('');
}
$('#job-filter').addEventListener('input', renderJobs);

async function scan() {
  if (!ONLINE) { alert('The engine is not running yet. Open the app at http://127.0.0.1:8770.'); return; }
  const btns = [$('#btn-scan'), $('#btn-scan2')];
  btns.forEach(b => b && (b.disabled = true, b.textContent = 'Scanning…'));
  try {
    const r = await api('/scan', { method: 'POST', body: '{}' });
    await loadJobs(); await loadStats();
    if ((r.employers || 0) === 0 && (r.custom_sources || 0) === 0) {
      alert('There is nowhere to look yet. Go to the Jobs tab and add a place — a careers page, LinkedIn, Indeed, or any employer site — then try again.');
      show('jobs');
    } else if (r.new === 0) {
      alert(`Checked ${r.checked} place${r.checked === 1 ? '' : 's'} — no new postings this time.` +
            (r.skipped_by_robots ? ` (${r.skipped_by_robots} skipped: they don't allow automated checking.)` : ''));
    } else {
      alert(`Found ${r.new} new lead${r.new === 1 ? '' : 's'} across ${r.checked} place${r.checked === 1 ? '' : 's'}.`);
    }
  } catch (e) { alert('Scan failed: ' + e.message); }
  btns.forEach(b => b && (b.disabled = false, b.textContent = 'Find jobs'));
}
$('#btn-scan').onclick = scan;
$('#btn-scan2').onclick = scan;

$('#btn-seed').onclick = async () => {
  if (!ONLINE) { alert('Connect the local engine first.'); return; }
  try { const r = await api('/employers/seed', { method: 'POST', body: '{}' });
        alert(`Loaded ${r.count} employers.`); await loadJobs(); }
  catch (e) { alert('Failed: ' + e.message); }
};

/* ---------- scan schedule ---------- */
if ($('#scan-schedule')) {
  const sel = $('#scan-schedule');
  sel.value = LS.get('scanEvery', '0');
  const note = () => {
    const h = +sel.value;
    $('#schedule-note').textContent = h
      ? `On — next automatic check about ${h}h after the last one.`
      : '';
  };
  note();
  sel.onchange = async () => {
    LS.set('scanEvery', sel.value);
    note();
    if (ONLINE) { try { await api('/schedule', { method: 'POST', body: JSON.stringify({ hours: +sel.value }) }); } catch {} }
  };
  // client-side timer as well, so it runs while the page is open
  setInterval(() => {
    const h = +LS.get('scanEvery', '0');
    if (!h || !ONLINE) return;
    const last = +LS.get('lastScan', '0');
    if (Date.now() - last > h * 3600e3) { LS.set('lastScan', Date.now()); scan(); }
  }, 60000);
}

/* ---------- self-upgrade ---------- */
$$('.idea').forEach(b => b.onclick = () => {
  const t = $('#chat-input-upgrade');
  t.value = b.textContent.trim();
  t.focus();
  t.style.height = 'auto';
  t.style.height = Math.min(t.scrollHeight, 190) + 'px';
});

const UPGRADE_BOX = { msgs: '#msgs-upgrade', input: '#chat-input-upgrade',
                      send: '#btn-send-upgrade', ctx: 'upgrade' };

async function runUpgrade(text) {
  addMsg('me', text, UPGRADE_BOX);
  const inp = $('#chat-input-upgrade');
  if (inp) { inp.value = ''; inp.style.height = 'auto'; }

  if (!ONLINE) { addMsg('bot', 'The engine is not running, so I cannot edit myself yet.', UPGRADE_BOX); return; }

  if (/^\s*undo\b/i.test(text)) return undoUpgrade();

  addPending(UPGRADE_BOX);
  try {
    const r = await api('/upgrade', { method: 'POST', body: JSON.stringify({ request: text }) });
    if (r.error) { setBotText(r.error, UPGRADE_BOX); return; }
    setBotText(`${r.message}\n\nReloading…`, UPGRADE_BOX);
    setTimeout(() => location.reload(), 1800);
  } catch (e) { setBotText('Could not change it: ' + e.message, UPGRADE_BOX); }
}

async function undoUpgrade() {
  if (!ONLINE) return;
  addPending(UPGRADE_BOX);
  try {
    const r = await api('/upgrade/undo', { method: 'POST', body: '{}' });
    setBotText(r.error || `${r.message}\n\nReloading…`, UPGRADE_BOX);
    if (!r.error) setTimeout(() => location.reload(), 1800);
  } catch (e) { setBotText('Could not undo: ' + e.message, UPGRADE_BOX); }
}

if ($('#btn-send-upgrade')) {
  $('#btn-send-upgrade').onclick = () => {
    const t = $('#chat-input-upgrade').value.trim();
    if (t) runUpgrade(t);
  };
  $('#chat-input-upgrade').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      const t = e.target.value.trim();
      if (t) runUpgrade(t);
    }
  });
}
if ($('#btn-undo-upgrade')) $('#btn-undo-upgrade').onclick = undoUpgrade;

/* ---------- custom sources ---------- */
async function loadSources() {
  const box = $('#src-list');
  if (!box) return;
  let list = LS.get('sources', []);
  if (ONLINE) { try { list = await api('/sources'); LS.set('sources', list); } catch {} }
  if (!list.length) {
    box.innerHTML = '<div class="empty" style="padding:22px"><div class="big">◎</div>' +
                    'No custom places yet. Add LinkedIn, Indeed, or any employer page above.</div>';
    return;
  }
  box.innerHTML = list.map((s, i) => `<div class="dl">
    <div class="what"><b>${esc(s.name)}</b>
      <span><span class="chip pink">${esc(s.kind || 'general')}</span>
      <a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url).slice(0, 62)}</a></span>
    </div>
    <button class="btn quiet" style="padding:6px 13px;font-size:12.5px" data-src="${s.id ?? i}">Remove</button>
  </div>`).join('');
  $$('[data-src]', box).forEach(b => b.onclick = async () => {
    if (ONLINE) { try { await api('/sources/delete', { method: 'POST', body: JSON.stringify({ id: +b.dataset.src }) }); } catch {} }
    const l = LS.get('sources', []).filter((x, i) => String(x.id ?? i) !== b.dataset.src);
    LS.set('sources', l); loadSources();
  });
}

if ($('#btn-add-src')) $('#btn-add-src').onclick = async () => {
  const name = $('#src-name').value.trim(), url = $('#src-url').value.trim();
  if (!name || !url) { alert('Give it a name and a web address.'); return; }
  const rec = { name, url: /^https?:/.test(url) ? url : 'https://' + url, kind: $('#src-kind').value };
  if (ONLINE) { try { await api('/sources', { method: 'POST', body: JSON.stringify(rec) }); } catch (e) { alert('Could not save: ' + e.message); } }
  else { const l = LS.get('sources', []); l.push(rec); LS.set('sources', l); }
  $('#src-name').value = ''; $('#src-url').value = '';
  loadSources();
};

/* ---------- applications ---------- */
async function loadApps() {
  if (!ONLINE) return;
  try {
    const apps = await api('/applications');
    $('#tag-apps').textContent = apps.length;
    const body = $('#apps-body');
    if (!apps.length) {
      body.innerHTML = `<tr><td colspan="6"><div class="empty"><div class="big">✈</div>No applications yet.</div></td></tr>`;
      return;
    }
    body.innerHTML = apps.map(a => `<tr>
      <td><b>${esc(a.employer)}</b></td>
      <td class="wrap-cell">${esc(a.title)}</td>
      <td><span class="chip ${a.status === 'submitted' ? 'ok' : a.status === 'review' ? 'warn' : ''}">${esc(a.status)}</span></td>
      <td>${esc(a.submitted_at || '—')}</td>
      <td class="wrap-cell">${esc(a.last_reply || '—')}</td>
      <td><button class="btn ghost" style="padding:5px 13px;font-size:12.5px" data-app="${a.id}">View</button></td>
    </tr>`).join('');
  } catch (e) { console.warn(e); }
}

/* ---------- stats ---------- */
async function loadStats() {
  if (!ONLINE) return;
  try {
    const s = await api('/stats');
    $('#s-open').textContent  = s.open ?? 0;
    $('#s-sent').textContent  = s.sent ?? 0;
    $('#s-soon').textContent  = s.closing_soon ?? 0;
    $('#s-reply').textContent = s.replies ?? 0;
    if (s.deadlines?.length) {
      $('#deadlines').innerHTML = s.deadlines.map(d => `<div class="dl">
        <div class="when ${d.days <= 7 ? 'soon' : ''}">${d.days < 0 ? 'PAST' : d.days + 'd'}</div>
        <div class="what"><b>${esc(d.what)}</b><span>${esc(d.who)}</span></div>
      </div>`).join('');
    }
    if (s.activity?.length) {
      $('#activity').innerHTML = s.activity.map(a => `<div class="dl">
        <div class="what"><b>${esc(a.text)}</b><span>${esc(a.at)}</span></div>
      </div>`).join('');
    }
  } catch (e) { console.warn(e); }
}

/* ---------- chat ---------- */
/* Two boxes - one on the dashboard, one on the Chat panel - sharing a
   single conversation, so a question asked in either appears in both. */
const BOXES = [
  { msgs: '#msgs',         input: '#chat-input',         send: '#btn-send',         ctx: 'general'   },
  { msgs: '#msgs-mini',    input: '#chat-input-mini',    send: '#btn-send-mini',    ctx: 'general'   },
  { msgs: '#msgs-jobs',    input: '#chat-input-jobs',    send: '#btn-send-jobs',    ctx: 'jobs'      },
  { msgs: '#msgs-docs',    input: '#chat-input-docs',    send: '#btn-send-docs',    ctx: 'documents' },
  { msgs: '#msgs-profile', input: '#chat-input-profile', send: '#btn-send-profile', ctx: 'profile'   },
];

function addMsg(role, text, only) {
  let last = null;
  (only ? [only] : BOXES).forEach(b => {
    const box = $(b.msgs);
    if (!box) return;
    const d = document.createElement('div');
    d.className = 'msg ' + role;
    d.textContent = text;
    box.appendChild(d);
    box.scrollTop = box.scrollHeight;
    last = d;
  });
  return last;
}

function setBotText(text, only) {
  (only ? [only] : BOXES).forEach(b => {
    const box = $(b.msgs);
    if (!box) return;
    const pending = box.querySelector('.msg.bot.pending');
    if (pending) { pending.textContent = text; pending.classList.remove('pending'); }
    box.scrollTop = box.scrollHeight;
  });
}

function addPending(only) {
  (only ? [only] : BOXES).forEach(b => {
    const box = $(b.msgs);
    if (!box) return;
    const d = document.createElement('div');
    d.className = 'msg bot pending';
    d.textContent = 'thinking…';
    box.appendChild(d);
    box.scrollTop = box.scrollHeight;
  });
}

BOXES.forEach(b => {
  const input = $(b.input), btn = $(b.send);
  if (!input || !btn) return;
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 190) + 'px';
  });
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input, b); }
  });
  btn.onclick = () => send(input);
});

async function send(input, box) {
  const text = input.value.trim();
  if (!text) return;
  const ctx = box ? box.ctx : 'general';
  addMsg('me', text, box);
  const i = $(box.input); if (i) { i.value = ''; i.style.height = 'auto'; }

  // Each context keeps its own short history, so the Jobs chat and the
  // Documents chat never bleed into each other.
  const key = 'chatlog.' + ctx;
  const log = LS.get(key, []);
  log.push({ role: 'user', text });
  LS.set(key, log.slice(-12));

  if (!ONLINE) {
    addMsg('bot', 'The engine is not running yet. Open the app at http://127.0.0.1:8770', box);
    return;
  }

  addPending(box);
  try {
    const r = await api('/chat', {
      method: 'POST',
      body: JSON.stringify({ message: text, history: log.slice(-8), context: ctx }),
    });
    setBotText(r.reply || '(no reply)', box);
    const l2 = LS.get(key, []);
    l2.push({ role: 'assistant', text: r.reply });
    LS.set(key, l2.slice(-12));
  } catch (e) {
    setBotText('Engine error: ' + e.message, box);
  }
}

function restoreChat() {
  // restore each context's own history into its own boxes
  BOXES.forEach(b => {
    const log = LS.get('chatlog.' + b.ctx, []);
    if (!log.length) return;
    const box = $(b.msgs);
    if (!box) return;
    box.innerHTML = '';
    log.slice(-20).forEach(m => addMsg(m.role === 'user' ? 'me' : 'bot', m.text, b));
  });
}

/* ---------- boot ---------- */
async function refreshAll() {
  await Promise.all([loadStats(), loadJobs(), loadApps(), loadSources(), loadCerts()]);
}

(async function boot() {
  loadProfile();
  renderCerts();
  renderFiles('#resume-list', 'resumes');
  renderFiles('#doc-list', 'documents');
  restoreChat();
  loadSources();
  if (await ping()) refreshAll();
  setInterval(ping, 20000);
})();
