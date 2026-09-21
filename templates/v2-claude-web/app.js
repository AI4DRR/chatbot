/* UNDRR Chatbot — UX concept v2
 * Static behaviour only: renders mock data, rail/recents/sources panels,
 * citation popover, responsive drawers and the preview-state switcher.
 * No network calls. */

(function () {
  'use strict';

  const M = window.MOCK;
  const ws = document.getElementById('ws');
  const $ = (id) => document.getElementById(id);
  const els = {
    recents: $('recents'), recentsList: $('recentsList'), railRecents: $('railRecents'), recentsPin: $('recentsPin'),
    railNew: $('railNew'), recentsNew: $('recentsNew'), mobileMenu: $('mobileMenu'),
    thread: $('thread'), starters: $('starters'), docTitleText: $('docTitleText'),
    sources: $('sourcesPanel'), sourcesBody: $('sourcesBody'), sourcesBtn: $('sourcesBtn'), sourcesBadge: $('sourcesBadge'), sourcesClose: $('sourcesClose'), sourcesNote: $('sourcesNote'),
    popover: $('popover'), popBody: $('popBody'), scrim: $('scrim'),
    composer: $('composer'), composerInput: $('composerInput'), stateSelect: $('stateSelect'),
  };
  const mq = window.matchMedia('(max-width: 900px)');
  const isMobile = () => mq.matches;
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[c]));
  const src = (n) => M.sources[n];

  /* ------------------------------------------------------------ render */
  function renderRecents() {
    els.recentsList.innerHTML = M.recents.map((r) => `
      <button class="recent${r.active ? ' is-active' : ''}" type="button" data-id="${r.id}" title="${esc(r.title)}">
        <b>${esc(r.title)}</b><small>${esc(r.when)}</small>
      </button>`).join('');
  }
  function renderStarters() {
    els.starters.innerHTML = M.starters.map((s) => `<button class="starter" type="button" data-text="${esc(s.k + ' ' + s.t)}"><b>${esc(s.k)}</b> ${esc(s.t)}</button>`).join('');
  }

  function pills(answer) {
    const shown = answer.sources.slice(0, 3);
    const rest = answer.sources.length - shown.length;
    return `<div class="src-pills">
      ${shown.map((n) => `<button class="src-pill${src(n).type === 'WEB' ? ' is-web' : ''}" type="button" data-src="${n}"><span class="n">${n}</span>${esc(src(n).domain)}</button>`).join('')}
      ${rest > 0 ? `<button class="src-pill more" type="button" data-answer="${answer.n}">+${rest} more</button>` : ''}
    </div>`;
  }
  function answerHtml(a) {
    return `
      <div class="answer" data-answer="${a.n}">
        <div class="prose">${a.html}</div>
        <div class="answer-foot">
          ${pills(a)}
          <div class="answer-actions">
            <button class="act" type="button" title="Copy" aria-label="Copy answer"><svg viewBox="0 0 20 20" aria-hidden="true"><rect x="7" y="7" width="9" height="9" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M13 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2" fill="none" stroke="currentColor" stroke-width="1.5"/></svg></button>
            <button class="act" type="button" title="Good answer" aria-label="Good answer"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6 9v7H3V9h3zm0 0l4-6a2 2 0 0 1 2 2v3h4a1.5 1.5 0 0 1 1.5 1.8l-1 5A1.5 1.5 0 0 1 15 16H6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg></button>
            <button class="act" type="button" title="Poor answer" aria-label="Poor answer"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M14 11V4h3v7h-3zm0 0l-4 6a2 2 0 0 1-2-2v-3H4a1.5 1.5 0 0 1-1.5-1.8l1-5A1.5 1.5 0 0 1 5 4h9" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg></button>
            <button class="act" type="button" title="Retry" aria-label="Regenerate"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 10a6 6 0 0 1 10.2-4.3L16 7.5M16 4v3.5h-3.5M16 10a6 6 0 0 1-10.2 4.3L4 12.5M4 16v-3.5h3.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
            <span class="answer-time">${esc(a.time)}</span>
          </div>
        </div>
      </div>`;
  }
  const userHtml = (t) => `<div class="you">${esc(t)}</div>`;
  const turnHtml = (t, tail) => `<section class="turn">${userHtml(t.user)}${tail !== undefined ? tail : answerHtml(t.answer)}</section>`;

  function thinkingHtml() {
    return `<div class="answer" aria-busy="true"><div class="thinking" role="status">
      ${M.loadingSteps.map((s, i) => `<div class="think-line ${i < 2 ? 'is-done' : i === 2 ? 'is-now' : ''}"><span class="dot"></span>${esc(s)}</div>`).join('')}
    </div><div class="ghost-lines" aria-hidden="true"><i></i><i></i><i></i></div></div>`;
  }
  function errorHtml() {
    const e = M.error;
    return `<div class="answer"><div class="notice" role="alert">
      <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M10 6v5M10 13.5v.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
      <div><h4>${esc(e.title)}</h4><p>${esc(e.detail)}</p><div class="code">${esc(e.code)}</div>
      <div class="notice-actions"><button class="btn btn-primary" type="button">Retry</button><button class="btn" type="button">Edit message</button></div></div>
    </div></div>`;
  }

  function renderSources() {
    const turns = M.conversation.turns;
    els.sourcesBody.innerHTML = turns.map((t) => `
      <div class="src-group" data-answer="${t.answer.n}">
        <h3>Answer ${t.answer.n} · ${t.answer.sources.length} sources</h3>
        ${t.answer.sources.map((n) => cardHtml(n, t.answer.n)).join('')}
      </div>`).join('');
  }
  function cardHtml(n, answerN) {
    const s = src(n);
    return `<div class="src-card" id="src-${answerN}-${n}" data-n="${n}">
      <span class="n" aria-hidden="true">${n}</span>
      <div>
        <div class="meta"><span class="kind${s.type === 'WEB' ? ' is-web' : ''}">${s.type === 'WEB' ? 'Web' : 'Knowledge base'}</span><span>${esc(s.domain)}</span><span>·</span><span>${esc(s.year)}</span></div>
        <h4><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a></h4>
        <p>${esc(s.snippet)}</p>
        <div class="foot"><span>${esc(s.section)}</span><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">Open ↗</a></div>
      </div></div>`;
  }

  /* ------------------------------------------------------------ panels */
  function openRecents() { ws.classList.add('recents-open'); els.railRecents.setAttribute('aria-expanded', 'true'); els.recents.setAttribute('aria-hidden', 'false'); if (isMobile()) els.scrim.hidden = false; }
  function closeRecents() { ws.classList.remove('recents-open'); els.railRecents.setAttribute('aria-expanded', 'false'); if (!ws.classList.contains('recents-pinned')) els.recents.setAttribute('aria-hidden', 'true'); syncScrim(); }
  function togglePin() {
    const pinned = ws.classList.toggle('recents-pinned');
    els.recentsPin.setAttribute('aria-pressed', pinned ? 'true' : 'false');
    if (pinned) { ws.classList.remove('recents-open'); els.recents.setAttribute('aria-hidden', 'false'); }
    try { localStorage.setItem('undrr_v2_recents_pinned', pinned ? '1' : '0'); } catch (_) {}
  }
  function openSources() { ws.classList.add('sources-open'); els.sources.setAttribute('aria-hidden', 'false'); els.sourcesBtn.setAttribute('aria-expanded', 'true'); if (isMobile()) { ws.classList.remove('recents-open'); els.scrim.hidden = false; } }
  function closeSources() { ws.classList.remove('sources-open'); els.sources.setAttribute('aria-hidden', 'true'); els.sourcesBtn.setAttribute('aria-expanded', 'false'); clearActive(); syncScrim(); }
  function syncScrim() {
    const need = isMobile() && (ws.classList.contains('recents-open') || ws.classList.contains('sources-open') || !els.popover.hidden);
    els.scrim.hidden = !need;
  }
  function closeAllOverlays() { closePopover(); closeRecents(); if (isMobile()) closeSources(); els.scrim.hidden = true; }

  function clearActive() {
    document.querySelectorAll('.src-card.is-active').forEach((el) => el.classList.remove('is-active'));
    document.querySelectorAll('.cite.is-active').forEach((el) => el.classList.remove('is-active'));
    els.sourcesNote.textContent = 'Grouped by answer. Select a citation in the conversation to jump to its source.';
  }
  function showInPanel(n, answerN) {
    openSources();
    clearActive();
    const card = document.getElementById(`src-${answerN}-${n}`) || document.querySelector(`.src-card[data-n="${n}"]`);
    if (card) { card.classList.add('is-active'); card.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }
    document.querySelectorAll(`.cite[data-src="${n}"]`).forEach((el) => el.classList.add('is-active'));
    els.sourcesNote.textContent = `Showing source [${n}] for answer ${answerN}.`;
  }
  function showAnswerInPanel(answerN) {
    openSources();
    clearActive();
    const g = document.querySelector(`.src-group[data-answer="${answerN}"]`);
    if (g) g.scrollIntoView({ block: 'start', behavior: 'smooth' });
    els.sourcesNote.textContent = `Sources for answer ${answerN}.`;
  }

  /* ------------------------------------------------------------ popover */
  let activeCite = null;
  function openPopover(citeEl) {
    activeCite = citeEl;
    const n = Number(citeEl.dataset.src); const s = src(n);
    const answerN = Number(citeEl.closest('.answer')?.dataset.answer || 1);
    els.popBody.innerHTML = `
      <div class="meta"><span class="kind${s.type === 'WEB' ? ' is-web' : ''}">${s.type === 'WEB' ? 'Web' : 'Knowledge base'}</span><span>${esc(s.domain)}</span><span>·</span><span>${esc(s.year)}</span></div>
      <h4>${esc(s.title)}</h4>
      <p>${esc(s.snippet)}</p>
      <div class="pop-actions">
        <a class="primary" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">Open source <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M8 5h7v7M15 5l-8 8" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></a>
        <button type="button" data-show="${n}" data-answer="${answerN}">Show in Sources</button>
      </div>`;
    document.querySelectorAll('.cite.is-active').forEach((el) => el.classList.remove('is-active'));
    citeEl.classList.add('is-active');
    els.popover.hidden = false;
    if (isMobile()) { els.scrim.hidden = false; return; }
    // Anchor below the citation, positioned relative to the workspace container
    // (the popover's offset parent), flipping left if it would leave the viewport.
    const r = citeEl.getBoundingClientRect();
    const base = ws.getBoundingClientRect();
    const popW = els.popover.offsetWidth;
    const vw = document.documentElement.clientWidth;
    let leftV = r.left - 20;
    leftV = Math.max(16, Math.min(leftV, vw - popW - 16));
    const left = leftV - base.left;
    els.popover.style.left = left + 'px';
    els.popover.style.top = (r.bottom - base.top + 10) + 'px';
    els.popover.querySelector('.pop-arrow').style.left = Math.max(12, r.left - leftV + 2) + 'px';
  }
  function closePopover() {
    if (els.popover.hidden) return;
    els.popover.hidden = true; activeCite = null;
    document.querySelectorAll('.cite.is-active').forEach((el) => el.classList.remove('is-active'));
    if (isMobile() && !ws.classList.contains('recents-open') && !ws.classList.contains('sources-open')) els.scrim.hidden = true;
  }

  /* ------------------------------------------------------------ states */
  const scrollToEnd = () => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'auto' });
  function setState(state) {
    document.body.dataset.state = state;
    const conv = M.conversation;
    closePopover(); closeSources(); closeRecents();
    document.querySelectorAll('.recent').forEach((el) => el.classList.toggle('is-active', state !== 'welcome' && el.dataset.id === conv.id));

    if (state === 'welcome') {
      els.docTitleText.textContent = 'New chat';
      els.thread.innerHTML = '';
      els.sourcesBadge.textContent = '0'; els.sourcesBadge.dataset.zero = 'true';
      window.scrollTo(0, 0);
    } else {
      els.docTitleText.textContent = conv.title;
      const total = conv.turns.reduce((a, t) => a + t.answer.sources.length, 0);
      els.sourcesBadge.textContent = String(total); delete els.sourcesBadge.dataset.zero;
      if (state === 'loading')      els.thread.innerHTML = turnHtml(conv.turns[0]) + turnHtml(conv.turns[1], thinkingHtml());
      else if (state === 'error')   els.thread.innerHTML = turnHtml(conv.turns[0]) + turnHtml(conv.turns[1], errorHtml());
      else                          els.thread.innerHTML = conv.turns.map((t) => turnHtml(t)).join('');
      scrollToEnd();
    }
    if (state === 'sources-open') showAnswerInPanel(2);
    if (state === 'citation') {
      const c = document.querySelector('.answer[data-answer="1"] .cite[data-src="2"]');
      if (c) { c.scrollIntoView({ block: 'center', behavior: 'auto' }); openPopover(c); }
    }
    if (els.stateSelect.value !== state) els.stateSelect.value = state;
  }

  /* ------------------------------------------------------------ events */
  els.railRecents.addEventListener('click', () => ws.classList.contains('recents-open') ? closeRecents() : openRecents());
  els.mobileMenu.addEventListener('click', openRecents);
  els.recentsPin.addEventListener('click', togglePin);
  els.railNew.addEventListener('click', () => setState('welcome'));
  els.recentsNew.addEventListener('click', () => { setState('welcome'); });
  els.sourcesBtn.addEventListener('click', () => ws.classList.contains('sources-open') ? closeSources() : (closePopover(), openSources()));
  els.sourcesClose.addEventListener('click', closeSources);
  els.scrim.addEventListener('click', closeAllOverlays);
  els.stateSelect.addEventListener('change', (e) => setState(e.target.value));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { if (!els.popover.hidden) closePopover(); else if (isMobile()) closeAllOverlays(); else { closeRecents(); closeSources(); } } });

  // Hover-to-peek recents on desktop (Claude-like); click still toggles
  els.railRecents.addEventListener('mouseenter', () => { if (!isMobile() && !ws.classList.contains('recents-pinned')) openRecents(); });
  els.recents.addEventListener('mouseleave', () => { if (!isMobile() && !ws.classList.contains('recents-pinned')) closeRecents(); });
  els.railRecents.addEventListener('mouseleave', (e) => { if (!isMobile() && !ws.classList.contains('recents-pinned') && !els.recents.contains(e.relatedTarget)) closeRecents(); });

  document.addEventListener('click', (e) => {
    const cite = e.target.closest('.cite');
    if (cite) { openPopover(cite); return; }
    const show = e.target.closest('[data-show]');
    if (show) { const n = Number(show.dataset.show), a = Number(show.dataset.answer); closePopover(); showInPanel(n, a); return; }
    if (!els.popover.hidden && !els.popover.contains(e.target)) closePopover();

    const pill = e.target.closest('.src-pill');
    if (pill) {
      const answerN = Number(pill.closest('.answer').dataset.answer);
      if (pill.dataset.src) showInPanel(Number(pill.dataset.src), answerN); else showAnswerInPanel(answerN);
      return;
    }
    const rec = e.target.closest('.recent');
    if (rec) { setState('conversation'); document.querySelectorAll('.recent').forEach((el) => el.classList.toggle('is-active', el === rec)); return; }
    const st = e.target.closest('.starter');
    if (st) { els.composerInput.value = st.dataset.text; els.composerInput.focus(); autoResize(); }
  });

  // Composer
  function autoResize() { els.composerInput.style.height = 'auto'; els.composerInput.style.height = Math.min(els.composerInput.scrollHeight, 240) + 'px'; }
  els.composerInput.addEventListener('input', autoResize);
  els.composerInput.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); els.composer.requestSubmit(); } });
  els.composer.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = els.composerInput.value.trim(); if (!text) return;
    if (document.body.dataset.state === 'welcome') { setState('conversation'); els.thread.innerHTML = ''; }
    els.thread.insertAdjacentHTML('beforeend', turnHtml({ user: text }, thinkingHtml()));
    els.composerInput.value = ''; autoResize(); scrollToEnd();
    setTimeout(() => { const busy = els.thread.querySelector('[aria-busy="true"]'); if (busy) busy.outerHTML = answerHtml(M.conversation.turns[1].answer); scrollToEnd(); }, 1600);
  });

  window.addEventListener('resize', () => { if (!els.popover.hidden && activeCite) openPopover(activeCite); });
  mq.addEventListener('change', () => { closeAllOverlays(); closeSources(); });

  /* ------------------------------------------------------------ init */
  renderRecents(); renderStarters(); renderSources();
  try { if (localStorage.getItem('undrr_v2_recents_pinned') === '1') { ws.classList.add('recents-pinned'); els.recentsPin.setAttribute('aria-pressed', 'true'); els.recents.setAttribute('aria-hidden', 'false'); } } catch (_) {}
  setState(document.body.dataset.state || 'conversation');
})();
