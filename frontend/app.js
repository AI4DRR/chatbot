/* AI4DRR Chatbot — production frontend (V1 ChatGPT-style layout).
 * Talks to the FastAPI backend at AI4DRR.API_BASE with the session cookie (identity.js):
 *   GET /auth/config · GET /auth/me · POST /auth/logout · Entra / local sign-in (identity.js)
 *   POST /login (new session) · GET /sessions · GET /sessions/{id} · POST /sessions/{id}/resume
 *   PATCH /sessions/{id}/title · POST /chat
 * One page scroll; sidebar and sources panel are fixed (see styles.css). */

(function () {
  'use strict';

  const CFG = window.AI4DRR;
  const MD = window.AI4DRR_MARKDOWN;
  const IDENTITY = window.AI4DRR_IDENTITY;

  const app = document.getElementById('app');
  const $ = (id) => document.getElementById(id);
  const els = {
    history: $('history'), suggestions: $('suggestions'), thread: $('thread'), chatTitle: $('chatTitle'),
    sourceList: $('sourceList'), sourcesEmpty: $('sourcesEmpty'), sourcesSub: $('sourcesSub'), sourcesCount: $('sourcesCount'),
    sourcesToggle: $('sourcesToggle'), sourcesClose: $('sourcesClose'), sources: $('sources'),
    sidebar: $('sidebar'), sidebarCollapse: $('sidebarCollapse'), sidebarOpen: $('sidebarOpen'), scrim: $('scrim'),
    composer: $('composer'), composerInput: $('composerInput'), sendBtn: $('sendBtn'), newChatBtn: $('newChatBtn'),
    login: $('login'), ssoBtn: $('ssoBtn'), ssoNote: $('ssoNote'),
    authViews: { signin: $('authSignIn'), reset: $('authReset') },
    localForm: $('localForm'), localSubmit: $('localSubmit'), localError: $('localError'),
    resetForm: $('resetForm'), resetSubmit: $('resetSubmit'), resetError: $('resetError'), resetDone: $('resetDone'),
    signOut: $('signOut'), userAvatar: $('userAvatar'), userName: $('userName'), userOrg: $('userOrg'), userChip: $('userChip'),
  };

  const mq = window.matchMedia('(max-width: 900px)');
  const isMobile = () => mq.matches;
  const esc = MD.esc;

  /* ---------------------------------------------------------------------
     State
     --------------------------------------------------------------------- */
  const state = {
    user: null,
    sessions: [],          // SessionInfo[] from GET /sessions
    sessionId: null,       // open session, or null while a new chat is pending
    pendingNew: false,     // "New chat" pressed: the session row is created on first send
    messages: [],          // [{id, role, content, sources}] of the open session
    busy: false,           // a turn is in flight
    panel: { messageId: null, sources: [] }, // what the Sources panel shows
    filter: 'all',
  };

  /* ---------------------------------------------------------------------
     API client — one place for the contract and its error shape
     --------------------------------------------------------------------- */
  class ApiError extends Error {
    constructor(status, detail) { super(detail); this.status = status; this.detail = detail; }
  }

  async function api(method, path, body) {
    let resp;
    try {
      resp = await fetch(CFG.API_BASE + path, {
        method,
        credentials: 'same-origin',
        headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (_) {
      throw new ApiError(0, 'The server could not be reached. Check your connection and try again.');
    }
    let data = null;
    try { data = await resp.json(); } catch (_) { data = null; }
    if (resp.status === 401 && state.user) {
      // The server-side session ended (logout elsewhere, expiry): back to sign-in without losing the page.
      onSignedOut('Your session has ended. Please sign in again.');
    }
    if (!resp.ok) {
      // FastAPI: {"detail": str | [...], "status_code"}; behind Drupal also {"error"} / {"raw"}.
      const d = data && (data.detail ?? data.error ?? data.raw);
      const detail = typeof d === 'string' ? d : Array.isArray(d) ? 'The request was not valid.' : `HTTP ${resp.status}`;
      throw new ApiError(resp.status, detail);
    }
    return data;
  }

  /* ---------------------------------------------------------------------
     Rendering — sidebar
     --------------------------------------------------------------------- */
  function groupLabel(iso) {
    const d = new Date(iso); const now = new Date();
    const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const diff = Math.round((day(now) - day(d)) / 86400000);
    if (diff <= 0) return 'Today';
    if (diff === 1) return 'Yesterday';
    if (diff < 7) return 'Previous 7 days';
    if (diff < 30) return 'Previous 30 days';
    return 'Older';
  }

  function renderHistory() {
    const order = ['Today', 'Yesterday', 'Previous 7 days', 'Previous 30 days', 'Older'];
    const groups = new Map();
    const sorted = [...state.sessions].sort((a, b) => new Date(b.last_message_at || b.created_at) - new Date(a.last_message_at || a.created_at));
    for (const s of sorted) {
      const g = groupLabel(s.last_message_at || s.created_at);
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(s);
    }
    if (!sorted.length) {
      els.history.innerHTML = '<p class="history-empty">No conversations yet. Ask your first question below.</p>';
      return;
    }
    els.history.innerHTML = order.filter((g) => groups.has(g)).map((g) => `
      <div class="history-group">
        <h3>${esc(g)}</h3>
        ${groups.get(g).map((s) => {
          const title = s.title && s.title !== CFG.DEFAULT_TITLE ? s.title : 'New chat';
          return `
          <button class="history-item${s.id === state.sessionId ? ' is-active' : ''}" type="button" data-id="${s.id}" title="${esc(title)}">
            <span>${esc(title)}</span>
            <i class="item-more" role="button" aria-label="Rename conversation" title="Rename" data-rename="${s.id}">···</i>
          </button>`;
        }).join('')}
      </div>`).join('');
  }

  function renderSuggestions() {
    els.suggestions.innerHTML = CFG.SUGGESTIONS.map((s) => `<button class="suggestion" type="button">${esc(s)}</button>`).join('');
  }

  function renderUser() {
    const u = state.user;
    const name = u.name || u.email;
    const initials = (u.name || u.email).split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('') || '?';
    els.userAvatar.textContent = initials;
    els.userName.textContent = name;
    els.userOrg.textContent = [u.department, u.unit].filter(Boolean).join(' · ') || u.email;
  }

  /* ---------------------------------------------------------------------
     Rendering — thread
     --------------------------------------------------------------------- */
  function assistantFooter(msg) {
    const n = msg.sources.length;
    const dots = msg.sources.slice(0, 3).map((s) => `<i class="${s.type === 'WEB' ? 'web' : 'kb'}"></i>`).join('');
    const time = msg.created_at ? new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
    return `
      <div class="msg-foot">
        ${n ? `<button class="msg-sources" type="button" data-message="${msg.id}" aria-label="Show ${n} sources for this answer">
          <span class="favicons">${dots}</span> ${n} source${n === 1 ? '' : 's'}
        </button>` : ''}
        <button class="msg-action" type="button" title="Copy answer" aria-label="Copy answer" data-copy="${msg.id}"><svg viewBox="0 0 20 20" aria-hidden="true"><rect x="7" y="7" width="9" height="9" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M13 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2" fill="none" stroke="currentColor" stroke-width="1.5"/></svg></button>
        <span class="msg-meta">${esc(time)}</span>
      </div>`;
  }

  function messageHtml(msg) {
    if (msg.role === 'user') {
      return `<div class="msg msg-user" data-id="${msg.id}"><div class="bubble">${esc(msg.content)}</div></div>`;
    }
    return `
      <div class="msg msg-assistant" data-id="${msg.id}">
        <div class="body">
          <div class="answer">${MD.render(msg.content, msg.sources.length)}</div>
          ${assistantFooter(msg)}
        </div>
      </div>`;
  }

  function thinkingHtml() {
    const steps = CFG.PROGRESS_STEPS.map((s, i) => `<div class="thinking-step${i === 0 ? ' is-current' : ''}" data-step="${i}"><span class="dot"></span><span>${esc(s.label)}</span></div>`).join('');
    return `
      <div class="msg msg-assistant" id="thinking" aria-busy="true">
        <div class="body">
          <div class="thinking" role="status">${steps}</div>
          <div class="skeleton" aria-hidden="true"><i></i><i></i><i></i></div>
        </div>
      </div>`;
  }

  function errorHtml(err) {
    const title = err.status === 0 ? 'Connection problem' : err.status >= 500 ? 'The assistant could not answer' : 'Request failed';
    return `
      <div class="msg msg-assistant" id="turnError">
        <div class="body">
          <div class="error-card" role="alert">
            <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M10 6v5M10 13.5v.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
            <div>
              <h4>${esc(title)}</h4>
              <p>${esc(err.detail || 'Something went wrong.')}</p>
              ${err.status ? `<div class="code">HTTP ${err.status}</div>` : ''}
              <div class="error-actions">
                <button class="btn btn-primary" type="button" data-retry>Retry</button>
                <button class="btn" type="button" data-edit>Edit message</button>
              </div>
            </div>
          </div>
        </div>
      </div>`;
  }

  function renderThread() {
    els.thread.innerHTML = state.messages.map(messageHtml).join('');
    // styles.css shows .welcome and hides .thread in the "welcome" state, and the reverse otherwise
    document.body.dataset.state = state.messages.length ? 'conversation' : 'welcome';
  }

  /* ---------------------------------------------------------------------
     Rendering — sources panel (per answer; [k] ↔ sources[k-1])
     --------------------------------------------------------------------- */
  function domainOf(url) { try { return new URL(url).hostname.replace(/^www\./, ''); } catch (_) { return ''; } }

  function renderSources() {
    const items = state.panel.sources.map((s, i) => ({ n: i + 1, ...s }));
    const list = items.filter((s) => state.filter === 'all' || s.type === state.filter);
    els.sourceList.innerHTML = list.map((s) => `
      <li class="source-card" id="src-${s.n}" data-n="${s.n}" data-type="${esc(s.type)}">
        <span class="num" aria-hidden="true">${s.n}</span>
        <div class="src-body">
          <div class="src-top">
            <span class="badge ${s.type === 'WEB' ? 'badge-web' : 'badge-kb'}">${s.type === 'WEB' ? 'Web' : 'Knowledge base'}</span>
            <span class="src-domain">${esc(domainOf(s.url))}</span>
          </div>
          <h3 class="src-title"><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title || s.url)}</a></h3>
          <div class="src-foot">
            <span></span>
            <a class="src-open" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">Open <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M8 5h7v7M15 5l-8 8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></a>
          </div>
        </div>
      </li>`).join('');
    els.sourcesEmpty.hidden = list.length > 0;
    els.sourcesCount.textContent = String(state.panel.sources.length);
    if (state.panel.sources.length) delete els.sourcesCount.dataset.zero; else els.sourcesCount.dataset.zero = 'true';
    document.querySelectorAll('.sources-filter .chip').forEach((c) => c.classList.toggle('is-on', c.dataset.filter === state.filter));
  }

  function setPanelToMessage(messageId) {
    const msg = state.messages.find((m) => m.id === messageId && m.role === 'assistant');
    state.panel = { messageId: msg ? msg.id : null, sources: msg ? msg.sources : [] };
    renderSources();
  }

  function setPanelToLatest() {
    const last = [...state.messages].reverse().find((m) => m.role === 'assistant');
    setPanelToMessage(last ? last.id : null);
    els.sourcesSub.textContent = 'For the latest answer';
  }

  function openSources() {
    if (isMobile()) { app.classList.add('drawer-sources'); app.classList.remove('drawer-sidebar'); els.scrim.hidden = false; }
    else app.classList.add('sources-open');
    els.sources.setAttribute('aria-hidden', 'false');
    els.sourcesToggle.setAttribute('aria-expanded', 'true');
  }
  function closeSources() {
    app.classList.remove('sources-open', 'drawer-sources');
    if (!app.classList.contains('drawer-sidebar')) els.scrim.hidden = true;
    els.sources.setAttribute('aria-hidden', 'true');
    els.sourcesToggle.setAttribute('aria-expanded', 'false');
    clearActive();
  }
  function toggleSources() {
    const open = isMobile() ? app.classList.contains('drawer-sources') : app.classList.contains('sources-open');
    open ? closeSources() : openSources();
  }
  function clearActive() {
    document.querySelectorAll('.source-card.is-active, .source-card.is-dim').forEach((el) => el.classList.remove('is-active', 'is-dim'));
    document.querySelectorAll('.cite.is-active').forEach((el) => el.classList.remove('is-active'));
  }

  // A citation [n] inside message M highlights source n of M's sources.
  function focusCitation(messageEl, n) {
    const messageId = messageEl.dataset.id;
    if (state.panel.messageId !== messageId) setPanelToMessage(messageId);
    state.filter = 'all'; renderSources();
    openSources(); clearActive();
    const card = document.getElementById('src-' + n);
    if (!card) return;
    card.classList.add('is-active');
    card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    messageEl.querySelectorAll(`.cite[data-src="${n}"]`).forEach((el) => el.classList.add('is-active'));
    els.sourcesSub.textContent = `Citation [${n}] highlighted`;
  }

  /* ---------------------------------------------------------------------
     Sessions
     --------------------------------------------------------------------- */
  async function loadSessions() {
    state.sessions = await api('GET', '/sessions'); // the server scopes the list to the signed-in user
    renderHistory();
  }

  async function openSession(id) {
    await api('POST', `/sessions/${encodeURIComponent(id)}/resume`); // validates the id (legacy contract)
    const history = await api('GET', `/sessions/${encodeURIComponent(id)}`);
    state.sessionId = id; state.pendingNew = false;
    state.messages = history.messages.map((m) => ({ id: m.id, role: m.role, content: m.content, sources: m.sources || [], created_at: m.created_at }));
    els.chatTitle.textContent = history.title && history.title !== CFG.DEFAULT_TITLE ? history.title : 'New chat';
    renderThread(); renderHistory(); setPanelToLatest(); closeSources();
    scrollToEnd();
  }

  function startNewChat() {
    state.sessionId = null; state.pendingNew = true; state.messages = [];
    els.chatTitle.textContent = 'New chat';
    renderThread(); renderHistory(); setPanelToLatest(); closeSources();
    window.scrollTo(0, 0);
    els.composerInput.focus();
  }

  async function ensureSession() {
    if (state.sessionId) return state.sessionId;
    const res = await api('POST', '/login'); // opens a session for the signed-in user (server-side identity)
    state.sessionId = res.session_id; state.pendingNew = false;
    return state.sessionId;
  }

  async function renameSession(id) {
    const current = state.sessions.find((s) => s.id === id);
    const proposed = window.prompt('Rename conversation', current && current.title && current.title !== CFG.DEFAULT_TITLE ? current.title : '');
    if (proposed === null) return;
    const title = proposed.trim();
    if (!title) return;
    try {
      const res = await api('PATCH', `/sessions/${encodeURIComponent(id)}/title`, { title });
      if (current) current.title = res.title;
      if (id === state.sessionId) els.chatTitle.textContent = res.title;
      renderHistory();
    } catch (err) {
      window.alert(`Could not rename: ${err.detail || err.message}`);
    }
  }

  /* ---------------------------------------------------------------------
     Chat turn
     --------------------------------------------------------------------- */
  let progressTimers = [];
  function startProgress() {
    stopProgress();
    CFG.PROGRESS_STEPS.forEach((s, i) => {
      if (i === 0) return;
      progressTimers.push(setTimeout(() => {
        document.querySelectorAll('#thinking .thinking-step').forEach((el, j) => {
          el.classList.toggle('is-done', j < i);
          el.classList.toggle('is-current', j === i);
        });
      }, s.after));
    });
  }
  function stopProgress() { progressTimers.forEach(clearTimeout); progressTimers = []; }

  function setBusy(busy) {
    state.busy = busy;
    els.composer.classList.toggle('is-busy', busy);
    els.sendBtn.disabled = busy;
    els.composerInput.readOnly = busy;
  }

  async function sendMessage(text) {
    if (state.busy || !text) return;
    const isFirstTurn = state.messages.length === 0;
    document.getElementById('turnError')?.remove();
    setBusy(true);
    // Optimistic user bubble; the server stores it only with a successful answer.
    const tempId = 'pending-' + Date.now();
    state.messages.push({ id: tempId, role: 'user', content: text, sources: [] });
    renderThread();
    els.thread.insertAdjacentHTML('beforeend', thinkingHtml());
    startProgress();
    scrollToEnd();

    try {
      const sessionId = await ensureSession();
      const res = await api('POST', '/chat', { message: text, session_id: sessionId });
      state.messages = state.messages.filter((m) => m.id !== tempId);
      state.messages.push({ id: 'u-' + res.message_id, role: 'user', content: text, sources: [] });
      state.messages.push({ id: res.message_id, role: 'assistant', content: res.response, sources: res.sources || [], created_at: res.timestamp });
      stopProgress();
      renderThread(); setPanelToLatest();
      scrollToEnd();
      if (isFirstTurn) await afterFirstTurn(text);
      else await loadSessions().catch(() => {});
    } catch (err) {
      stopProgress();
      document.getElementById('thinking')?.remove();
      const e = err instanceof ApiError ? err : new ApiError(0, err.message);
      els.thread.insertAdjacentHTML('beforeend', errorHtml(e));
      const card = document.getElementById('turnError');
      card.querySelector('[data-retry]').addEventListener('click', () => {
        state.messages = state.messages.filter((m) => m.id !== tempId); renderThread();
        sendMessage(text);
      });
      card.querySelector('[data-edit]').addEventListener('click', () => {
        state.messages = state.messages.filter((m) => m.id !== tempId); renderThread();
        els.composerInput.value = text; autoResize(); els.composerInput.focus();
      });
      scrollToEnd();
    } finally {
      setBusy(false);
    }
  }

  // Legacy convention: title a new session from its first question, then refresh the sidebar.
  async function afterFirstTurn(text) {
    const words = text.split(/\s+/);
    const title = words.slice(0, CFG.TITLE_WORDS).join(' ') + (words.length > CFG.TITLE_WORDS ? '…' : '');
    try {
      const res = await api('PATCH', `/sessions/${encodeURIComponent(state.sessionId)}/title`, { title });
      els.chatTitle.textContent = res.title;
    } catch (_) { /* title is cosmetic; the session and turn are already stored */ }
    await loadSessions().catch(() => {});
  }

  /* ---------------------------------------------------------------------
     Layout helpers (from the template)
     --------------------------------------------------------------------- */
  function scrollToEnd() { window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'auto' }); }
  function openSidebar() {
    if (isMobile()) { app.classList.add('drawer-sidebar'); app.classList.remove('drawer-sources'); els.scrim.hidden = false; }
    else { app.classList.remove('sidebar-collapsed'); persistSidebar(false); }
    syncSidebarToggle();
  }
  function collapseSidebar() {
    if (isMobile()) { app.classList.remove('drawer-sidebar'); if (!app.classList.contains('drawer-sources')) els.scrim.hidden = true; }
    else { app.classList.add('sidebar-collapsed'); persistSidebar(true); }
    syncSidebarToggle();
  }
  function toggleSidebar() { if (app.classList.contains('sidebar-collapsed')) openSidebar(); else collapseSidebar(); }
  function syncSidebarToggle() {
    const collapsed = app.classList.contains('sidebar-collapsed');
    els.sidebarCollapse.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    els.sidebarCollapse.title = collapsed ? 'Expand history' : 'Collapse history';
    els.sidebarCollapse.setAttribute('aria-label', collapsed ? 'Expand history panel' : 'Collapse history panel');
  }
  function closeDrawers() {
    app.classList.remove('drawer-sidebar', 'drawer-sources');
    els.scrim.hidden = true;
    els.sources.setAttribute('aria-hidden', 'true');
    els.sourcesToggle.setAttribute('aria-expanded', 'false');
  }
  function persistSidebar(collapsed) { try { localStorage.setItem('ai4drr.sidebarCollapsed', collapsed ? '1' : '0'); } catch (_) {} }
  function restoreSidebar() { try { if (localStorage.getItem('ai4drr.sidebarCollapsed') === '1') app.classList.add('sidebar-collapsed'); } catch (_) {} }
  function autoResize() {
    els.composerInput.style.height = 'auto';
    els.composerInput.style.height = Math.min(els.composerInput.scrollHeight, 220) + 'px';
  }

  /* ---------------------------------------------------------------------
     Sign-in / sign-out (server-side sessions — see identity.js)
     --------------------------------------------------------------------- */
  function showLogin(message) {
    document.body.dataset.state = 'login';
    els.login.hidden = false;
    showAuthView('signin');
    if (message) { els.localError.textContent = message; els.localError.hidden = false; }
    window.scrollTo(0, 0);
  }

  function onSignedOut(message) {
    state.user = null; state.sessions = []; state.messages = []; state.sessionId = null;
    showLogin(message);
  }

  function showAuthView(name) {
    const local = document.body.dataset.localAuth === 'on';
    const view = local ? name : 'signin';
    Object.entries(els.authViews).forEach(([k, el]) => { el.hidden = k !== view; });
    [els.localError, els.resetError, els.resetDone].forEach((el) => { el.hidden = true; el.textContent = ''; });
    const first = els.authViews[view].querySelector('input');
    if (first && view !== 'signin') first.focus({ preventScroll: true });
  }

  function setBusyBtn(btn, busy) { btn.disabled = busy; btn.classList.toggle('is-busy', busy); }
  function showAuthError(el, err) { el.textContent = err.detail || err.message; el.hidden = false; }

  // Which methods the server offers: drives the Microsoft button and the local forms.
  async function applyAuthConfig() {
    let cfg = { entra_enabled: false, local_auth_enabled: false };
    try { cfg = await IDENTITY.config(); } catch (_) { /* leave everything disabled */ }
    document.body.dataset.localAuth = cfg.local_auth_enabled ? 'on' : 'off';
    els.ssoBtn.setAttribute('aria-disabled', cfg.entra_enabled ? 'false' : 'true');
    els.ssoNote.textContent = cfg.entra_enabled
      ? 'You will be redirected to Microsoft to sign in.'
      : 'Microsoft sign-in is not configured on this deployment.';
    if (!cfg.entra_enabled && !cfg.local_auth_enabled) {
      els.ssoNote.textContent += ' No sign-in method is available; contact an administrator.';
    }
    return cfg;
  }

  async function enterApp(user) {
    state.user = user;
    els.login.hidden = true;
    renderUser();
    startNewChat();
    try { await loadSessions(); } catch (err) {
      els.history.innerHTML = `<p class="history-empty">Could not load conversations: ${esc(err.detail || err.message)}</p>`;
    }
  }

  els.ssoBtn.addEventListener('click', (e) => {
    if (els.ssoBtn.getAttribute('aria-disabled') === 'true') { e.preventDefault(); return; }
    e.preventDefault();
    window.location.assign(IDENTITY.entraLoginUrl()); // full-page redirect to Microsoft (B2C user flow); the callback returns to "/"
  });

  els.localForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    els.localError.hidden = true;
    const f = els.localForm;
    if (!f.email.value.trim() || !f.password.value) { showAuthError(els.localError, { detail: 'Enter your email and password.' }); return; }
    setBusyBtn(els.localSubmit, true);
    try {
      const user = await IDENTITY.localLogin({ email: f.email.value.trim(), password: f.password.value });
      f.password.value = '';
      await enterApp(user);
    } catch (err) { showAuthError(els.localError, err); }
    finally { setBusyBtn(els.localSubmit, false); }
  });

  els.resetForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    els.resetError.hidden = true; els.resetDone.hidden = true;
    setBusyBtn(els.resetSubmit, true);
    try {
      const out = await IDENTITY.localForgot({ email: els.resetForm.email.value.trim() });
      // Always the same generic sentence from the server, whether or not the account exists.
      els.resetDone.textContent = (out && out.detail) || "If an account with that email can reset its password, we've sent instructions.";
      els.resetDone.hidden = false;
    } catch (err) { showAuthError(els.resetError, err); }
    finally { setBusyBtn(els.resetSubmit, false); }
  });

  els.login.addEventListener('click', (e) => {
    const link = e.target.closest('[data-auth-view]');
    if (!link) return;
    e.preventDefault();
    showAuthView(link.dataset.authView);
  });

  els.signOut.addEventListener('click', async () => {
    let out = null;
    try { out = await IDENTITY.signOut(); } catch (_) { /* the cookie is cleared server-side on the next request anyway */ }
    // A Microsoft (B2C) session is closed at Microsoft too; it returns to the configured logout URI.
    if (out && out.redirect) { window.location.assign(out.redirect); return; }
    onSignedOut();
  });

  // Back from Microsoft with a reason (never a secret): show it once on the sign-in screen, clean the URL.
  const AUTH_ERRORS = {
    cancelled: 'Microsoft sign-in was cancelled.',
    failed: 'Microsoft sign-in could not be completed. Please try again.',
    disabled: 'This account has been disabled. Contact an AI4DRR Chatbot administrator.',
  };
  function authErrorFromUrl() {
    const params = new URLSearchParams(location.search);
    const code = params.get('auth_error');
    if (!code) return null;
    params.delete('auth_error');
    const clean = location.pathname + (params.toString() ? '?' + params.toString() : '') + location.hash;
    history.replaceState(null, '', clean);
    return AUTH_ERRORS[code] || AUTH_ERRORS.failed;
  }

  /* ---------------------------------------------------------------------
     Events
     --------------------------------------------------------------------- */
  els.sourcesToggle.addEventListener('click', toggleSources);
  els.sourcesClose.addEventListener('click', closeSources);
  els.sidebarCollapse.addEventListener('click', toggleSidebar);
  els.sidebarOpen.addEventListener('click', openSidebar);
  els.scrim.addEventListener('click', closeDrawers);
  els.newChatBtn.addEventListener('click', () => { startNewChat(); if (isMobile()) closeDrawers(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { if (isMobile()) closeDrawers(); else closeSources(); } });
  mq.addEventListener('change', () => { closeDrawers(); closeSources(); });

  document.addEventListener('click', async (e) => {
    const cite = e.target.closest('.cite');
    if (cite) { focusCitation(cite.closest('.msg'), Number(cite.dataset.src)); return; }

    const chip = e.target.closest('.msg-sources');
    if (chip) {
      setPanelToMessage(chip.dataset.message); state.filter = 'all'; renderSources(); openSources(); clearActive();
      els.sourcesSub.textContent = `${state.panel.sources.length} sources for this answer`;
      return;
    }

    const copy = e.target.closest('[data-copy]');
    if (copy) {
      const msg = state.messages.find((m) => m.id === copy.dataset.copy);
      if (msg && navigator.clipboard) navigator.clipboard.writeText(msg.content).catch(() => {});
      return;
    }

    const filter = e.target.closest('.sources-filter .chip');
    if (filter) { clearActive(); state.filter = filter.dataset.filter; renderSources(); return; }

    const rename = e.target.closest('[data-rename]');
    if (rename) { e.stopPropagation(); renameSession(rename.dataset.rename); return; }

    const item = e.target.closest('.history-item');
    if (item) {
      if (state.busy) return;
      try { await openSession(item.dataset.id); } catch (err) { window.alert(`Could not open conversation: ${err.detail || err.message}`); }
      if (isMobile()) closeDrawers();
      return;
    }

    const sug = e.target.closest('.suggestion');
    if (sug) { els.composerInput.value = sug.textContent; els.composerInput.focus(); autoResize(); }
  });

  els.composerInput.addEventListener('input', autoResize);
  els.composerInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); els.composer.requestSubmit(); }
  });
  els.composer.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = els.composerInput.value.trim();
    if (!text || state.busy) return;
    els.composerInput.value = ''; autoResize();
    sendMessage(text);
  });

  /* ---------------------------------------------------------------------
     Init
     --------------------------------------------------------------------- */
  renderSuggestions();
  restoreSidebar();
  syncSidebarToggle();
  renderSources();
  (async () => {
    const authError = authErrorFromUrl();
    await applyAuthConfig();
    let user = null;
    try { user = await IDENTITY.current(); } catch (_) { user = null; }
    if (user) { await enterApp(user); return; }
    showLogin();
    if (authError) { els.ssoNote.textContent = authError; els.localError.textContent = authError; els.localError.hidden = false; }
  })();
})();
