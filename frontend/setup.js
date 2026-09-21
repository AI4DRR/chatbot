/* Invitation account setup (/account-setup#token=…).
 * The token is read from the URL fragment and only ever sent in JSON request bodies. */
(function () {
  'use strict';

  const API = window.AI4DRR.API_BASE;
  const $ = (id) => document.getElementById(id);
  const views = ['setupChecking', 'setupForm', 'setupSuccess', 'setupInvalid'];
  const show = (id) => views.forEach((v) => { $(v).hidden = v !== id; });
  const token = new URLSearchParams(location.hash.replace(/^#/, '')).get('token') || '';

  async function post(path, body) {
    const resp = await fetch(API + path, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    let data = null;
    try { data = await resp.json(); } catch (_) { data = null; }
    return { status: resp.status, data };
  }

  function invalid(reason, plain) {
    if (reason) $('invalidReason').textContent = plain ? reason : reason + ' Ask an AI4DRR Chatbot administrator for a new invitation.';
    show('setupInvalid');
  }

  async function init() {
    if (!token) { invalid('This page must be opened from the link in your invitation e-mail.'); return; }
    const { status, data } = await post('/auth/setup/check', { token });
    if (status === 200) { $('email').value = data.email; show('setupForm'); $('firstName').focus(); return; }
    invalid(data && typeof data.detail === 'string' ? data.detail + '.' : '', status === 403);
  }

  $('form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = $('form'); const err = $('error'); err.hidden = true;
    if (!f.first_name.value.trim() || !f.last_name.value.trim()) { err.textContent = 'First name and last name are required.'; err.hidden = false; return; }
    if (f.password.value.length < 8) { err.textContent = 'Use at least 8 characters for your password.'; err.hidden = false; return; }
    if (f.password.value !== f.confirm_password.value) { err.textContent = 'Passwords do not match.'; err.hidden = false; return; }
    $('submit').disabled = true; $('submit').classList.add('is-busy');
    try {
      const { status, data } = await post('/auth/setup', {
        token, password: f.password.value, confirm_password: f.confirm_password.value,
        first_name: f.first_name.value.trim(), last_name: f.last_name.value.trim(), department: f.department.value.trim() || null,
      });
      if (status === 201) { f.password.value = ''; f.confirm_password.value = ''; show('setupSuccess'); return; }
      if (status === 410) { invalid(data && data.detail ? data.detail + '.' : ''); return; }
      err.textContent = data && typeof data.detail === 'string' ? data.detail : 'Account setup failed (HTTP ' + status + ').';
      err.hidden = false;
    } finally {
      $('submit').disabled = false; $('submit').classList.remove('is-busy');
    }
  });

  init();
})();
