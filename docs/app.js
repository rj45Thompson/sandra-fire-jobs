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
function renderCerts() {
  const certs = LS.get('certs', []);
  const box = $('#cert-list');
  $('#tag-docs').textContent = certs.length;
  if (!certs.length) {
    box.innerHTML = '<div class="empty"><div class="big">❋</div>No certifications recorded yet.</div>';
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
    return `<div class="dl">
      <div class="what">
        <b>${esc(c.name)}</b>
        <span><span class="chip ${st}">${esc(c.status)}</span> ${chip}</span>
      </div>
      <button class="btn quiet" style="padding:6px 13px;font-size:12.5px" data-del="${i}">Remove</button>
    </div>`;
  }).join('');
  $$('[data-del]', box).forEach(b => b.onclick = () => {
    const c = LS.get('certs', []); c.splice(+b.dataset.del, 1); LS.set('certs', c); renderCerts();
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
          await api('/upload', {
            method: 'POST',
            body: JSON.stringify({ kind, filename: f.name, content_b64: b64 }),
          });
          list[list.length - 1].uploaded = true;
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
  if (!ONLINE) { alert('Connect the local engine first — click Connect in the sidebar.'); return; }
  const btns = [$('#btn-scan'), $('#btn-scan2')];
  btns.forEach(b => b && (b.disabled = true, b.textContent = 'Scanning…'));
  try {
    await api('/scan', { method: 'POST', body: '{}' });
    await loadJobs(); await loadStats();
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
  { msgs: '#msgs',      input: '#chat-input',      send: '#btn-send' },
  { msgs: '#msgs-mini', input: '#chat-input-mini', send: '#btn-send-mini' },
];

function addMsg(role, text) {
  let last = null;
  BOXES.forEach(b => {
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

function setBotText(text) {
  BOXES.forEach(b => {
    const box = $(b.msgs);
    if (!box) return;
    const pending = box.querySelector('.msg.bot.pending');
    if (pending) { pending.textContent = text; pending.classList.remove('pending'); }
    box.scrollTop = box.scrollHeight;
  });
}

function addPending() {
  BOXES.forEach(b => {
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
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input); }
  });
  btn.onclick = () => send(input);
});

async function send(input) {
  const text = input.value.trim();
  if (!text) return;
  addMsg('me', text);
  BOXES.forEach(b => { const i = $(b.input); if (i) { i.value = ''; i.style.height = 'auto'; } });

  const log = LS.get('chatlog', []);
  log.push({ role: 'user', text, at: new Date().toISOString() });
  LS.set('chatlog', log);

  if (!ONLINE) {
    addMsg('bot', `The local engine is not running, so I cannot answer yet.

Start it with:
  py backend/server.py

then click Connect in the sidebar. Your message is saved and will still be here.`);
    return;
  }

  addPending();
  try {
    const r = await api('/chat', {
      method: 'POST',
      body: JSON.stringify({ message: text, history: log.slice(-20) }),
    });
    setBotText(r.reply || '(no reply)');
    log.push({ role: 'assistant', text: r.reply, at: new Date().toISOString() });
    LS.set('chatlog', log);
  } catch (e) {
    setBotText('Engine error: ' + e.message);
  }
}

function restoreChat() {
  const log = LS.get('chatlog', []);
  if (!log.length) return;
  BOXES.forEach(b => { const box = $(b.msgs); if (box) box.innerHTML = ''; });
  log.slice(-40).forEach(m => addMsg(m.role === 'user' ? 'me' : 'bot', m.text));
}

/* ---------- boot ---------- */
async function refreshAll() {
  await Promise.all([loadStats(), loadJobs(), loadApps()]);
}

(async function boot() {
  loadProfile();
  renderCerts();
  renderFiles('#resume-list', 'resumes');
  renderFiles('#doc-list', 'documents');
  restoreChat();
  if (await ping()) refreshAll();
  setInterval(ping, 20000);
})();
