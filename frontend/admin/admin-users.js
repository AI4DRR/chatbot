/* Chatbot Management — Users (/admin/users).
 * Served only to administrators; every call below is guarded server-side too.
 * Active: GET /api/admin/users. Pending: GET /api/admin/invitations; Invite POST /api/admin/invitations;
 * Resend/Revoke POST /api/admin/invitations/{id}/resend|revoke. View GET /api/admin/users/{id};
 * Disable/Enable POST /api/admin/users/{id}/disable|enable (server-side guarded; buttons are only a convenience). */
(function () {
  'use strict';

  const IDENTITY = window.AI4DRR_IDENTITY;
  const API = window.AI4DRR.API_BASE;
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[c]));
  const $ = (id) => document.getElementById(id);
  const fmt = (iso) => new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });

  class ApiError extends Error { constructor(status, detail) { super(detail); this.status = status; this.detail = detail; } }

  async function api(method, path, body) {
    const resp = await fetch(API + path, {
      method, credentials: 'same-origin',
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (resp.status === 401) { window.location.replace('/admin/login'); throw new ApiError(401, 'not signed in'); }
    if (resp.status === 403) { window.location.replace('/admin/login?denied=1'); throw new ApiError(403, 'not an administrator'); }
    let data = null;
    try { data = await resp.json(); } catch (_) { data = null; }
    if (!resp.ok) {
      const d = data && data.detail;
      throw new ApiError(resp.status, typeof d === 'string' ? d : Array.isArray(d) ? 'Please check the form.' : 'HTTP ' + resp.status);
    }
    return data;
  }

  let toastTimer = null;
  function toast(text) {
    const el = $('toast'); el.textContent = text; el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
  }

  function renderAdmin(me) {
    const name = me.name || me.email;
    $('adminName').textContent = name;
    $('adminAvatar').textContent = name.split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('') || '?';
  }

  let me = null;
  let localAccounts = true; // LOCAL_AUTH_ENABLED (GET /api/auth/config): inviting creates local passwords
  const displayName = (u) => u.name || [u.first_name, u.last_name].filter(Boolean).join(' ') || '—';

  function toggleButton(u, cls) {
    if (u.disabled) return `<button class="${cls}" type="button" data-action="enable" data-id="${u.id}" data-email="${esc(u.email)}">Enable</button>`;
    if (me && u.id === me.id) return `<button class="${cls}" type="button" disabled title="You cannot disable your own account">Disable</button>`;
    return `<button class="${cls} btn-danger" type="button" data-action="disable" data-id="${u.id}" data-email="${esc(u.email)}">Disable</button>`;
  }

  function renderUsers(users) {
    $('countActive').textContent = String(users.length);
    $('activeRows').innerHTML = users.map((u) => `
      <tr data-id="${u.id}"${u.disabled ? ' class="is-disabled"' : ''}>
        <td class="cell-name">${esc(displayName(u))}${u.is_admin ? ' <span class="status-pill">Administrator</span>' : ''}</td>
        <td>${esc(u.email)}</td>
        <td class="cell-muted">${esc(u.department || '—')}</td>
        <td>${u.disabled ? '<span class="status-pill disabled">Disabled</span>' : '<span class="status-pill enabled">Enabled</span>'}</td>
        <td class="col-actions"><span class="row-actions">
          <button class="btn btn-sm" type="button" data-action="view" data-id="${u.id}" data-email="${esc(u.email)}">View</button>
          ${toggleButton(u, 'btn btn-sm')}
        </span></td>
      </tr>`).join('');
    $('activeEmpty').hidden = users.length > 0;
  }

  // --- View dialog ---
  let viewed = null;
  function closeView() { $('viewScrim').hidden = true; $('viewModal').hidden = true; viewed = null; }
  $('viewClose').addEventListener('click', closeView);
  $('viewScrim').addEventListener('click', closeView);
  function renderView(u) {
    viewed = u;
    const yes = (b) => (b ? 'Yes' : 'No');
    const when = (iso) => (iso ? fmt(iso) : '—');
    $('viewTitle').textContent = displayName(u);
    $('viewSub').textContent = u.email;
    const rows = [
      ['Status', u.disabled ? `Disabled since ${when(u.disabled_at)}` : 'Enabled'],
      ['Administrator', yes(u.is_admin)],
      ['Department', u.department || '—'],
      ['Unit', u.unit || '—'],
      ['Password sign-in', u.has_password ? 'Set' : 'None'],
      ['Microsoft sign-in', u.entra_linked ? 'Linked' : 'Not linked'],
      ['Chats', String(u.chat_sessions)],
      ['Last chat activity', when(u.last_chat_at)],
      ['Signed-in sessions', String(u.signed_in_sessions)],
      ['Last seen', when(u.last_seen_at)],
      ['Created', when(u.created_at)],
      ['Updated', when(u.updated_at)],
    ];
    $('viewBody').innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
    const t = $('viewToggle');
    if (me && u.id === me.id) { t.hidden = true; }
    else { t.hidden = false; t.textContent = u.disabled ? 'Enable' : 'Disable'; t.className = u.disabled ? 'btn btn-primary' : 'btn btn-danger'; }
  }
  async function openView(id) {
    $('viewError').hidden = true; $('viewBody').innerHTML = ''; $('viewTitle').textContent = 'User'; $('viewSub').textContent = '';
    $('viewToggle').hidden = true; $('viewScrim').hidden = false; $('viewModal').hidden = false;
    try { renderView(await api('GET', `/admin/users/${encodeURIComponent(id)}`)); }
    catch (err) { if (err.status !== 401 && err.status !== 403) { $('viewError').textContent = err.detail; $('viewError').hidden = false; } }
    $('viewClose').focus();
  }
  async function setStatus(id, email, action) {
    if (action === 'disable' && !window.confirm(`Disable ${email}? They will be signed out everywhere and cannot sign in until enabled again. Their chats are kept.`)) return false;
    const u = await api('POST', `/admin/users/${encodeURIComponent(id)}/${action}`);
    toast(u.disabled ? `${u.email} disabled` : `${u.email} enabled`);
    await loadUsers();
    return true;
  }
  $('viewToggle').addEventListener('click', async () => {
    if (!viewed) return;
    try {
      if (await setStatus(viewed.id, viewed.email, viewed.disabled ? 'enable' : 'disable')) await openView(viewed.id);
    } catch (err) { if (err.status !== 401 && err.status !== 403) { $('viewError').textContent = err.detail; $('viewError').hidden = false; } }
  });

  function renderPending(invitations) {
    $('countPending').textContent = String(invitations.length);
    $('pendingRows').innerHTML = invitations.map((inv) => `
      <tr data-id="${inv.id}">
        <td class="cell-name">${esc(inv.email)}${inv.grants_admin ? ' <span class="status-pill">Administrator</span>' : ''}</td>
        <td class="cell-muted">${esc(fmt(inv.created_at))}</td>
        <td>${inv.status === 'expired' ? '<span class="status-pill expired">Expired</span>' : `<span class="status-pill">${esc(fmt(inv.expires_at))}</span>`}</td>
        <td class="col-actions"><span class="row-actions">
          ${localAccounts ? `<button class="btn btn-sm" type="button" data-action="resend" data-id="${inv.id}" data-email="${esc(inv.email)}">Resend invitation</button>` : ''}
          <button class="btn btn-sm btn-danger" type="button" data-action="revoke" data-id="${inv.id}" data-email="${esc(inv.email)}">Revoke</button>
        </span></td>
      </tr>`).join('');
    $('pendingEmpty').hidden = invitations.length > 0;
  }

  async function loadUsers() {
    try { renderUsers(await api('GET', '/admin/users')); $('activeError').hidden = true; }
    catch (err) { if (err.status !== 401 && err.status !== 403) { $('activeError').textContent = 'Could not load users: ' + err.detail; $('activeError').hidden = false; } }
  }
  async function loadPending() {
    try { renderPending(await api('GET', '/admin/invitations')); $('pendingError').hidden = true; }
    catch (err) { if (err.status !== 401 && err.status !== 403) { $('pendingError').textContent = 'Could not load invitations: ' + err.detail; $('pendingError').hidden = false; } }
  }

  function setTab(name) {
    document.querySelectorAll('.admin-tab').forEach((t) => {
      const on = t.dataset.tab === name;
      t.classList.toggle('is-active', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    $('panelActive').hidden = name !== 'active';
    $('panelPending').hidden = name !== 'pending';
  }

  // --- Invite dialog ---
  function openInvite() { $('inviteScrim').hidden = false; $('inviteModal').hidden = false; $('inviteError').hidden = true; $('inviteEmail').value = ''; $('inviteEmail').focus(); }
  function closeInvite() { $('inviteScrim').hidden = true; $('inviteModal').hidden = true; }
  $('inviteBtn').addEventListener('click', openInvite);
  $('inviteCancel').addEventListener('click', closeInvite);
  $('inviteScrim').addEventListener('click', closeInvite);
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!$('inviteModal').hidden) closeInvite();
    if (!$('viewModal').hidden) closeView();
  });
  $('inviteForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = $('inviteEmail'); const err = $('inviteError'); err.hidden = true;
    const email = input.value.trim();
    if (!email || !input.validity.valid) { err.textContent = 'Enter a valid email address.'; err.hidden = false; return; }
    $('inviteSubmit').disabled = true;
    try {
      const inv = await api('POST', '/admin/invitations', { email });
      closeInvite(); setTab('pending'); await loadPending();
      toast(`Invitation sent to ${inv.email}`);
    } catch (e2) {
      if (e2.status !== 401 && e2.status !== 403) { err.textContent = e2.detail; err.hidden = false; }
    } finally { $('inviteSubmit').disabled = false; }
  });

  // --- Row actions ---
  document.addEventListener('click', async (e) => {
    const tab = e.target.closest('.admin-tab');
    if (tab) { setTab(tab.dataset.tab); return; }
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const id = btn.dataset.id; const email = btn.dataset.email;
    try {
      if (btn.dataset.action === 'view') { await openView(id); return; }
      if (btn.dataset.action === 'disable' || btn.dataset.action === 'enable') {
        btn.disabled = true;
        try { await setStatus(id, email, btn.dataset.action); } finally { btn.disabled = false; }
        return;
      }
      if (btn.dataset.action === 'resend') {
        btn.disabled = true;
        await api('POST', `/admin/invitations/${encodeURIComponent(id)}/resend`);
        toast(`Invitation re-sent to ${email} (new link, 24 hours)`);
      } else if (btn.dataset.action === 'revoke') {
        if (!window.confirm(`Revoke the invitation for ${email}? The link will stop working.`)) return;
        await api('POST', `/admin/invitations/${encodeURIComponent(id)}/revoke`);
        toast('Invitation revoked');
      }
      await loadPending();
    } catch (err) {
      btn.disabled = false;
      if (err.status !== 401 && err.status !== 403) window.alert(err.detail);
    }
  });

  $('signOut').addEventListener('click', async () => {
    let out = null;
    try { out = await IDENTITY.signOut(); } catch (_) { /* cookie is invalid either way */ }
    window.location.replace((out && out.redirect) || '/admin/login');
  });

  (async () => {
    try { me = await api('GET', '/admin/me'); renderAdmin(me); } catch (_) { return; }
    try { localAccounts = (await IDENTITY.config()).local_auth_enabled; } catch (_) { localAccounts = true; }
    if (!localAccounts) {
      // Microsoft-only deployment: nothing to invite to — people sign in with Microsoft and appear here.
      $('inviteBtn').disabled = true;
      $('inviteBtn').title = 'Local accounts are disabled (LOCAL_AUTH_ENABLED=false); people sign in with Microsoft.';
      $('pendingNote').hidden = false;
    }
    await Promise.all([loadUsers(), loadPending()]);
    if (new URLSearchParams(location.search).get('tab') === 'pending') setTab('pending');
  })();
})();
