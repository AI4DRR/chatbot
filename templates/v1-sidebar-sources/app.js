/* AI4DRR Chatbot — UX template v1
 * Static behaviour only: renders mock data, wires panel toggles, citation
 * focus, responsive drawers, the mock sign-in / reset views and
 * the template-only state switcher. No network calls. */

(function () {
  'use strict';

  const M = window.MOCK;
  const app = document.getElementById('app');
  const els = {
    history: document.getElementById('history'),
    suggestions: document.getElementById('suggestions'),
    thread: document.getElementById('thread'),
    threadWrap: document.getElementById('threadWrap'),
    chatTitle: document.getElementById('chatTitle'),
    sourceList: document.getElementById('sourceList'),
    sourcesEmpty: document.getElementById('sourcesEmpty'),
    sourcesSub: document.getElementById('sourcesSub'),
    sourcesCount: document.getElementById('sourcesCount'),
    sourcesToggle: document.getElementById('sourcesToggle'),
    sourcesClose: document.getElementById('sourcesClose'),
    sources: document.getElementById('sources'),
    sidebar: document.getElementById('sidebar'),
    sidebarCollapse: document.getElementById('sidebarCollapse'),
    sidebarOpen: document.getElementById('sidebarOpen'),
    scrim: document.getElementById('scrim'),
    composer: document.getElementById('composer'),
    composerInput: document.getElementById('composerInput'),
    stateSelect: document.getElementById('stateSelect'),
    newChatBtn: document.getElementById('newChatBtn'),
    login: document.getElementById('login'), ssoBtn: document.getElementById('ssoBtn'), signOut: document.getElementById('signOut'),
    authViews: { signin: document.getElementById('authSignIn'), reset: document.getElementById('authReset') },
    localForm: document.getElementById('localForm'), localSubmit: document.getElementById('localSubmit'), localError: document.getElementById('localError'),
    resetForm: document.getElementById('resetForm'), resetSubmit: document.getElementById('resetSubmit'), resetError: document.getElementById('resetError'), resetDone: document.getElementById('resetDone'),
    localAuthToggle: document.getElementById('localAuthToggle'),
  };

  const mq = window.matchMedia('(max-width: 900px)');
  const isMobile = () => mq.matches;

  /* ---------------------------------------------------------------------
     Rendering helpers
     --------------------------------------------------------------------- */
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[c]));
  const sourceByN = (n) => M.sources.find((s) => s.n === n);

  function renderHistory() {
    els.history.innerHTML = M.history.map((g) => `
      <div class="history-group">
        <h3>${esc(g.group)}</h3>
        ${g.items.map((it) => `
          <button class="history-item${it.active ? ' is-active' : ''}" type="button" data-id="${it.id}" title="${esc(it.title)}">
            <span>${esc(it.title)}</span>
            <i class="item-more" aria-hidden="true">···</i>
          </button>`).join('')}
      </div>`).join('');
  }

  function renderSuggestions() {
    els.suggestions.innerHTML = M.suggestions.map((s) => `<button class="suggestion" type="button">${esc(s)}</button>`).join('');
  }

  function assistantFooter(msg) {
    const n = msg.sources.length;
    const dots = msg.sources.slice(0, 3).map((k) => `<i class="${sourceByN(k).type === 'KB' ? 'kb' : 'web'}"></i>`).join('');
    return `
      <div class="msg-foot">
        <button class="msg-sources" type="button" data-sources="${msg.sources.join(',')}" aria-label="Show ${n} sources for this answer">
          <span class="favicons">${dots}</span> ${n} source${n === 1 ? '' : 's'}
        </button>
        <button class="msg-action" type="button" title="Copy answer" aria-label="Copy answer"><svg viewBox="0 0 20 20" aria-hidden="true"><rect x="7" y="7" width="9" height="9" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M13 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2" fill="none" stroke="currentColor" stroke-width="1.5"/></svg></button>
        <button class="msg-action" type="button" title="Good answer" aria-label="Good answer"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6 9v7H3V9h3zm0 0l4-6a2 2 0 0 1 2 2v3h4a1.5 1.5 0 0 1 1.5 1.8l-1 5A1.5 1.5 0 0 1 15 16H6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg></button>
        <button class="msg-action" type="button" title="Poor answer" aria-label="Poor answer"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M14 11V4h3v7h-3zm0 0l-4 6a2 2 0 0 1-2-2v-3H4a1.5 1.5 0 0 1-1.5-1.8l1-5A1.5 1.5 0 0 1 5 4h9" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg></button>
        <span class="msg-meta">${esc(msg.meta.time)}</span>
      </div>`;
  }

  function messageHtml(msg) {
    if (msg.role === 'user') {
      return `<div class="msg msg-user"><div class="bubble">${esc(msg.text)}</div></div>`;
    }
    return `
      <div class="msg msg-assistant">
        <div class="body">
          <div class="answer">${msg.html}</div>
          ${assistantFooter(msg)}
        </div>
      </div>`;
  }

  function thinkingHtml() {
    const steps = M.loadingSteps.map((s, i) => {
      const cls = i < 2 ? 'is-done' : i === 2 ? 'is-current' : '';
      return `<div class="thinking-step ${cls}"><span class="dot"></span><span>${esc(s)}</span></div>`;
    }).join('');
    return `
      <div class="msg msg-assistant" aria-busy="true">
        <div class="body">
          <div class="thinking" role="status">${steps}</div>
          <div class="skeleton" aria-hidden="true"><i></i><i></i><i></i></div>
        </div>
      </div>`;
  }

  function errorHtml() {
    const e = M.error;
    return `
      <div class="msg msg-assistant">
        <div class="body">
          <div class="error-card" role="alert">
            <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M10 6v5M10 13.5v.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
            <div>
              <h4>${esc(e.title)}</h4>
              <p>${esc(e.detail)}</p>
              <div class="code">${esc(e.code)}</div>
              <div class="error-actions">
                <button class="btn btn-primary" type="button">Retry</button>
                <button class="btn" type="button">Edit message</button>
              </div>
            </div>
          </div>
        </div>
      </div>`;
  }

  function renderThread(messages, tail) {
    els.thread.innerHTML = messages.map(messageHtml).join('') + (tail || '');
  }

  function renderSources(filter) {
    const f = filter || 'all';
    const list = M.sources.filter((s) => f === 'all' || s.type === f);
    els.sourceList.innerHTML = list.map((s) => `
      <li class="source-card" id="src-${s.n}" data-n="${s.n}" data-type="${s.type}">
        <span class="num" aria-hidden="true">${s.n}</span>
        <div class="src-body">
          <div class="src-top">
            <span class="badge ${s.type === 'KB' ? 'badge-kb' : 'badge-web'}">${s.type === 'KB' ? 'Knowledge base' : 'Web'}</span>
            <span class="src-domain">${esc(s.domain)}</span>
          </div>
          <h3 class="src-title"><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a></h3>
          <p class="src-snippet">${esc(s.snippet)}</p>
          <div class="src-foot">
            <span>${esc(s.section)}</span>
            <a class="src-open" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">Open <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M8 5h7v7M15 5l-8 8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></a>
          </div>
        </div>
      </li>`).join('');
    els.sourcesEmpty.hidden = list.length > 0;
  }

  /* ---------------------------------------------------------------------
     Panels
     --------------------------------------------------------------------- */
  function openSources() {
    if (isMobile()) {
      app.classList.add('drawer-sources');
      app.classList.remove('drawer-sidebar');
      els.scrim.hidden = false;
    } else {
      app.classList.add('sources-open');
    }
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

  function openSidebar() {
    if (isMobile()) {
      app.classList.add('drawer-sidebar');
      app.classList.remove('drawer-sources');
      els.scrim.hidden = false;
    } else {
      app.classList.remove('sidebar-collapsed');
      persistSidebar(false);
    }
    syncSidebarToggle();
  }
  function collapseSidebar() {
    if (isMobile()) {
      app.classList.remove('drawer-sidebar');
      if (!app.classList.contains('drawer-sources')) els.scrim.hidden = true;
    } else {
      app.classList.add('sidebar-collapsed');
      persistSidebar(true);
    }
    syncSidebarToggle();
  }
  // Desktop: the one toggle in the sidebar switches expanded <-> rail.
  function toggleSidebar() {
    if (app.classList.contains('sidebar-collapsed')) openSidebar(); else collapseSidebar();
  }
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
  function persistSidebar(collapsed) {
    try { localStorage.setItem('undrr_tpl_sidebar_collapsed', collapsed ? '1' : '0'); } catch (_) {}
  }
  function restoreSidebar() {
    try { if (localStorage.getItem('undrr_tpl_sidebar_collapsed') === '1') app.classList.add('sidebar-collapsed'); } catch (_) {}
  }

  /* ---------------------------------------------------------------------
     Citation → source focus
     --------------------------------------------------------------------- */
  function clearActive() {
    document.querySelectorAll('.source-card.is-active, .source-card.is-dim').forEach((el) => el.classList.remove('is-active', 'is-dim'));
    document.querySelectorAll('.cite.is-active').forEach((el) => el.classList.remove('is-active'));
    els.sourcesSub.textContent = 'For the latest answer';
  }

  function focusSource(n, opts) {
    openSources();
    clearActive();
    // Reset the filter so the requested source is guaranteed to be visible.
    setFilter('all');
    const card = document.getElementById('src-' + n);
    if (!card) return;
    card.classList.add('is-active');
    card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    document.querySelectorAll(`.cite[data-src="${n}"]`).forEach((el) => el.classList.add('is-active'));
    els.sourcesSub.textContent = `Citation [${n}] highlighted`;
    if (opts && opts.focus) card.querySelector('.src-title a').focus({ preventScroll: true });
  }

  function highlightSet(ns) {
    openSources();
    clearActive();
    setFilter('all');
    document.querySelectorAll('.source-card').forEach((el) => {
      el.classList.toggle('is-dim', !ns.includes(Number(el.dataset.n)));
    });
    const first = document.getElementById('src-' + ns[0]);
    if (first) first.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    els.sourcesSub.textContent = `${ns.length} sources for this answer`;
  }

  function setFilter(f) {
    document.querySelectorAll('.sources-filter .chip').forEach((c) => c.classList.toggle('is-on', c.dataset.filter === f));
    renderSources(f);
  }

  /* ---------------------------------------------------------------------
     Template preview states
     --------------------------------------------------------------------- */
  function setState(state) {
    // The reset state exists only while local auth is enabled.
    if (state === 'login-reset' && !localAuthEnabled()) state = 'login';
    document.body.dataset.state = state;
    const conv = M.conversation;
    closeSources();
    if (isMobile()) closeDrawers();

    if (state.startsWith('login')) {
      els.login.hidden = false;
      renderLogin(state);
      window.scrollTo(0, 0);
      if (els.stateSelect.value !== state) els.stateSelect.value = state;
      return;
    }
    els.login.hidden = true;

    switch (state) {
      case 'welcome':
        els.chatTitle.textContent = 'New chat';
        els.thread.innerHTML = '';
        els.sourcesCount.textContent = '0';
        els.sourcesCount.dataset.zero = 'true';
        document.querySelectorAll('.history-item').forEach((el) => el.classList.remove('is-active'));
        break;
      case 'conversation':
      case 'sources-open':
        els.chatTitle.textContent = conv.title;
        renderThread(conv.messages);
        markActiveHistory(conv.id);
        break;
      case 'loading':
        els.chatTitle.textContent = conv.title;
        renderThread(conv.messages.slice(0, 3), thinkingHtml());
        markActiveHistory(conv.id);
        break;
      case 'error':
        els.chatTitle.textContent = conv.title;
        renderThread(conv.messages.slice(0, 3), errorHtml());
        markActiveHistory(conv.id);
        break;
    }

    if (state !== 'welcome') {
      els.sourcesCount.textContent = String(M.sources.length);
      delete els.sourcesCount.dataset.zero;
    }
    if (state === 'sources-open') focusSource(2);

    scrollToEnd();
    if (els.stateSelect.value !== state) els.stateSelect.value = state;
  }

  // The page itself is the primary scroll container (ChatGPT model).
  function scrollToEnd() {
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'auto' });
  }

  function markActiveHistory(id) {
    document.querySelectorAll('.history-item').forEach((el) => el.classList.toggle('is-active', el.dataset.id === id));
  }

  /* ---------------------------------------------------------------------
     Login (mock). Two ways in, no identity is collected or sent anywhere:
       - organizational SSO (Microsoft / Entra target): one action, brief
         signing-in state, then welcome;
       - local email + password, with a reset view (no self-registration:
         access is by invitation, see admin-users.html / account-setup.html) —
         shown only while local auth is enabled (MOCK.config.LOCAL_AUTH_ENABLED,
         overridable from the preview bar). Client-side checks only.
     --------------------------------------------------------------------- */
  const SIGN_IN_DELAY = 1100;

  function setLocalAuth(enabled) {
    document.body.dataset.localAuth = enabled ? 'on' : 'off';
    els.localAuthToggle.checked = enabled;
  }
  const localAuthEnabled = () => document.body.dataset.localAuth === 'on';

  // The reset view only exists for local auth; otherwise fall back to sign-in.
  function showAuthView(name) {
    const view = localAuthEnabled() ? name : 'signin';
    Object.entries(els.authViews).forEach(([k, el]) => { el.hidden = k !== view; });
    clearAuthMessages();
    const first = els.authViews[view].querySelector('input');
    if (first && view !== 'signin') first.focus({ preventScroll: true });
  }
  function clearAuthMessages() {
    [els.localError, els.resetError].forEach((el) => { el.hidden = true; el.textContent = ''; });
    els.resetDone.hidden = true;
  }
  function showAuthError(el, text) { el.textContent = text; el.hidden = false; }

  function setBusy(btn, busy) {
    btn.disabled = busy;
    btn.classList.toggle('is-busy', busy);
  }
  function setLoginBusy(busy) { setBusy(els.ssoBtn, busy); }

  function renderLogin(state) {
    setLoginBusy(state === 'login-loading');
    showAuthView(state === 'login-reset' ? 'reset' : 'signin');
  }

  // Mock completion shared by SSO and local sign-in: no redirect or request.
  function completeSignIn(btn) {
    setBusy(btn, true);
    document.body.dataset.state = 'login-loading';
    setTimeout(() => { setBusy(btn, false); setState('welcome'); }, SIGN_IN_DELAY);
  }

  els.ssoBtn.addEventListener('click', () => completeSignIn(els.ssoBtn));
  els.signOut.addEventListener('click', () => setState('login'));

  // Minimal client-side checks so the mock demonstrates error presentation.
  const requiredFilled = (form) => Array.from(form.querySelectorAll('[required]')).every((i) => i.value.trim() !== '');
  const emailValid = (input) => input.validity.valid && input.value.trim() !== '';

  els.localForm.addEventListener('submit', (e) => {
    e.preventDefault();
    clearAuthMessages();
    if (!requiredFilled(els.localForm)) return showAuthError(els.localError, 'Enter your email and password.');
    if (!emailValid(els.localForm.email)) return showAuthError(els.localError, 'Enter a valid email address.');
    completeSignIn(els.localSubmit);
  });

  els.resetForm.addEventListener('submit', (e) => {
    e.preventDefault();
    clearAuthMessages();
    if (!emailValid(els.resetForm.email)) return showAuthError(els.resetError, 'Enter a valid email address.');
    setBusy(els.resetSubmit, true);
    setTimeout(() => { setBusy(els.resetSubmit, false); els.resetDone.hidden = false; }, 900);
  });

  // View links (Forgot password / Create account / Sign in / Back)
  els.login.addEventListener('click', (e) => {
    const link = e.target.closest('[data-auth-view]');
    if (!link) return;
    e.preventDefault();
    const view = link.dataset.authView;
    const state = view === 'reset' ? 'login-reset' : 'login';
    setState(state);
  });

  els.localAuthToggle.addEventListener('change', (e) => {
    setLocalAuth(e.target.checked);
    if (document.body.dataset.state.startsWith('login')) setState(document.body.dataset.state);
  });

  /* ---------------------------------------------------------------------
     Events
     --------------------------------------------------------------------- */
  els.sourcesToggle.addEventListener('click', toggleSources);
  els.sourcesClose.addEventListener('click', closeSources);
  els.sidebarCollapse.addEventListener('click', toggleSidebar);
  els.sidebarOpen.addEventListener('click', openSidebar);
  els.scrim.addEventListener('click', closeDrawers);
  els.stateSelect.addEventListener('change', (e) => setState(e.target.value));
  els.newChatBtn.addEventListener('click', () => setState('welcome'));

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { if (isMobile()) closeDrawers(); else closeSources(); }
  });

  // Delegated: citations, per-message source chips, history items, suggestions, filters
  document.addEventListener('click', (e) => {
    const cite = e.target.closest('.cite');
    if (cite) { focusSource(Number(cite.dataset.src), { focus: false }); return; }

    const chip = e.target.closest('.msg-sources');
    if (chip) { highlightSet(chip.dataset.sources.split(',').map(Number)); return; }

    const filter = e.target.closest('.sources-filter .chip');
    if (filter) { clearActive(); setFilter(filter.dataset.filter); return; }

    const item = e.target.closest('.history-item');
    if (item) {
      setState('conversation');
      markActiveHistory(item.dataset.id);
      if (isMobile()) closeDrawers();
      return;
    }

    const sug = e.target.closest('.suggestion');
    if (sug) { els.composerInput.value = sug.textContent; els.composerInput.focus(); autoResize(); return; }
  });

  // Composer: auto-resize, Enter to send (mock: shows loading state)
  function autoResize() {
    els.composerInput.style.height = 'auto';
    els.composerInput.style.height = Math.min(els.composerInput.scrollHeight, 220) + 'px';
  }
  els.composerInput.addEventListener('input', autoResize);
  els.composerInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); els.composer.requestSubmit(); }
  });
  els.composer.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = els.composerInput.value.trim();
    if (!text) return;
    // Mock send: append the user's message, show the thinking state, then the canned answer.
    if (document.body.dataset.state === 'welcome') setState('conversation');
    els.thread.insertAdjacentHTML('beforeend', messageHtml({ role: 'user', text }));
    els.thread.insertAdjacentHTML('beforeend', thinkingHtml());
    els.composerInput.value = ''; autoResize();
    scrollToEnd();
    setTimeout(() => {
      const busy = els.thread.querySelector('[aria-busy="true"]');
      if (busy) busy.outerHTML = messageHtml(M.conversation.messages[3]);
      scrollToEnd();
    }, 1600);
  });

  mq.addEventListener('change', () => { closeDrawers(); closeSources(); });

  /* ---------------------------------------------------------------------
     Init
     --------------------------------------------------------------------- */
  renderHistory();
  renderSuggestions();
  renderSources('all');
  restoreSidebar();
  syncSidebarToggle();
  setLocalAuth(Boolean(M.config && M.config.LOCAL_AUTH_ENABLED));
  setState(document.body.dataset.state || 'conversation');
})();
