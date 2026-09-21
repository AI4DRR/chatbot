/* Chatbot Management — Users (UX template v1).
 * Mock data only. Invite / resend / revoke / view / disable change the in-memory
 * lists to show the intended lifecycle; nothing is sent anywhere. The backend for
 * invitations does not exist yet (see the banner in admin-users.html). */
(function () {
  'use strict';

  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[c]));
  const $ = (id) => document.getElementById(id);
  const DAY = 24 * 3600 * 1000;
  const now = Date.now();

  // Mock data — illustrative only.
  const active = [
    { id: 'u1', name: 'Amina Mwangi', email: 'amina.mwangi@example.org', department: 'UNDRR · ROAP', disabled: false },
    { id: 'u2', name: 'Diego Ferreira', email: 'diego.ferreira@example.org', department: 'UNDRR · ROAMC', disabled: false },
    { id: 'u3', name: 'Mei Lin', email: 'mei.lin@example.org', department: 'UNDRR', disabled: false },
    { id: 'u4', name: 'Samuel Okoro', email: 'samuel.okoro@example.org', department: 'Partner · IFRC', disabled: true },
  ];
  const pending = [
    { id: 'i1', email: 'fatima.haddad@example.org', invited: now - 3 * 3600 * 1000 },
    { id: 'i2', email: 'jonas.berg@example.org', invited: now - 20 * 3600 * 1000 },
    { id: 'i3', email: 'k.nakamura@example.org', invited: now - 2 * DAY }, // expired: > 24 h
  ];

  const fmtDate = (t) => new Date(t).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
  const expiresAt = (inv) => inv.invited + DAY;

  function renderActive() {
    $('countActive').textContent = String(active.length);
    $('activeRows').innerHTML = active.map((u) => `
      <tr data-id="${u.id}">
        <td class="cell-name">${esc(u.name)}${u.disabled ? ' <span class="status-pill disabled">Disabled</span>' : ''}</td>
        <td>${esc(u.email)}</td>
        <td class="cell-muted">${esc(u.department || '—')}</td>
        <td class="col-actions"><span class="row-actions">
          <button class="btn btn-sm" type="button" data-action="view" data-id="${u.id}">View</button>
          ${u.disabled
            ? `<button class="btn btn-sm" type="button" data-action="enable" data-id="${u.id}">Enable</button>`
            : `<button class="btn btn-sm btn-danger" type="button" data-action="disable" data-id="${u.id}">Disable</button>`}
        </span></td>
      </tr>`).join('');
    $('activeEmpty').hidden = active.length > 0;
  }

  function renderPending() {
    $('countPending').textContent = String(pending.length);
    $('pendingRows').innerHTML = pending.map((inv) => {
      const expired = expiresAt(inv) < now;
      return `
      <tr data-id="${inv.id}">
        <td class="cell-name">${esc(inv.email)}</td>
        <td class="cell-muted">${esc(fmtDate(inv.invited))}</td>
        <td>${expired ? '<span class="status-pill expired">Expired</span>' : `<span class="status-pill">${esc(fmtDate(expiresAt(inv)))}</span>`}</td>
        <td class="col-actions"><span class="row-actions">
          <button class="btn btn-sm" type="button" data-action="resend" data-id="${inv.id}">Resend invitation</button>
          <button class="btn btn-sm btn-danger" type="button" data-action="revoke" data-id="${inv.id}">Revoke</button>
        </span></td>
      </tr>`;
    }).join('');
    $('pendingEmpty').hidden = pending.length > 0;
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

  let toastTimer = null;
  function toast(text) {
    const el = $('toast');
    el.textContent = text; el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
  }

  // --- Invite dialog (template-only: adds to the pending list) ---
  function openInvite() { $('inviteScrim').hidden = false; $('inviteModal').hidden = false; $('inviteError').hidden = true; $('inviteEmail').value = ''; $('inviteEmail').focus(); }
  function closeInvite() { $('inviteScrim').hidden = true; $('inviteModal').hidden = true; }
  $('inviteBtn').addEventListener('click', openInvite);
  $('inviteCancel').addEventListener('click', closeInvite);
  $('inviteScrim').addEventListener('click', closeInvite);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('inviteModal').hidden) closeInvite(); });
  $('inviteForm').addEventListener('submit', (e) => {
    e.preventDefault();
    const input = $('inviteEmail');
    const email = input.value.trim();
    const err = $('inviteError');
    if (!email || !input.validity.valid) { err.textContent = 'Enter a valid email address.'; err.hidden = false; return; }
    if (pending.some((p) => p.email.toLowerCase() === email.toLowerCase()) || active.some((u) => u.email.toLowerCase() === email.toLowerCase())) {
      err.textContent = 'This email already has an account or a pending invitation.'; err.hidden = false; return;
    }
    pending.unshift({ id: 'i' + Date.now(), email, invited: Date.now() });
    renderPending(); closeInvite(); setTab('pending');
    toast(`Invitation prepared for ${email} (template only — no email is sent)`);
  });

  // --- Row actions (template-only) ---
  document.addEventListener('click', (e) => {
    const tab = e.target.closest('.admin-tab');
    if (tab) { setTab(tab.dataset.tab); return; }
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const id = btn.dataset.id;
    switch (btn.dataset.action) {
      case 'resend': {
        const inv = pending.find((p) => p.id === id);
        if (inv) { inv.invited = Date.now(); renderPending(); toast(`Invitation to ${inv.email} would be re-sent (template only)`); }
        break;
      }
      case 'revoke': {
        const i = pending.findIndex((p) => p.id === id);
        if (i >= 0 && window.confirm(`Revoke the invitation for ${pending[i].email}?`)) { pending.splice(i, 1); renderPending(); toast('Invitation revoked (template only)'); }
        break;
      }
      case 'disable': {
        const u = active.find((x) => x.id === id);
        if (u && window.confirm(`Disable access for ${u.name}? They will not be able to sign in.`)) { u.disabled = true; renderActive(); toast(`${u.name} disabled (template only)`); }
        break;
      }
      case 'enable': {
        const u = active.find((x) => x.id === id);
        if (u) { u.disabled = false; renderActive(); toast(`${u.name} enabled (template only)`); }
        break;
      }
      case 'view': {
        const u = active.find((x) => x.id === id);
        if (u) window.alert(`${u.name}\n${u.email}\n${u.department || '—'}\nStatus: ${u.disabled ? 'Disabled' : 'Active'}\n\n(View is a placeholder in this template.)`);
        break;
      }
    }
  });

  renderActive();
  renderPending();
  setTab(new URLSearchParams(location.search).get('tab') === 'pending' ? 'pending' : 'active');
})();
