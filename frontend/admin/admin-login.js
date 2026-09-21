/* Chatbot Management doorway (/admin/login).
 * Same sign-in as the chatbot (identity.js). After any successful sign-in the page asks
 * GET /api/admin/me: 200 → /admin/users, 403 → the access-denied state (the normal session
 * stays valid for the chatbot), 401 → stay on the form. */
(function () {
  'use strict';

  const IDENTITY = window.AI4DRR_IDENTITY;
  const $ = (id) => document.getElementById(id);
  const views = ['authSignIn', 'authDenied', 'authChecking'];
  const show = (id) => views.forEach((v) => { $(v).hidden = v !== id; });
  const setBusy = (btn, busy) => { btn.disabled = busy; btn.classList.toggle('is-busy', busy); };
  const error = (text) => { $('localError').textContent = text; $('localError').hidden = !text; };

  async function adminCheck(user) {
    // The server is the authority: only users.is_admin=true gets the management page.
    const resp = await fetch(window.AI4DRR.API_BASE + '/admin/me', { credentials: 'same-origin' });
    if (resp.status === 200) { window.location.replace('/admin/users'); return; }
    if (resp.status === 403) { $('deniedWho').textContent = user.email; show('authDenied'); return; }
    show('authSignIn');
    if (resp.status !== 401) error('Could not verify your account (HTTP ' + resp.status + ').');
  }

  async function init() {
    let cfg = { entra_enabled: false, local_auth_enabled: false };
    try { cfg = await IDENTITY.config(); } catch (_) { /* leave disabled */ }
    document.body.dataset.localAuth = cfg.local_auth_enabled ? 'on' : 'off';
    $('ssoBtn').setAttribute('aria-disabled', cfg.entra_enabled ? 'false' : 'true');
    $('ssoNote').textContent = cfg.entra_enabled
      ? 'You will be redirected to Microsoft to sign in.'
      : 'Microsoft sign-in is not configured on this deployment.';

    let user = null;
    try { user = await IDENTITY.current(); } catch (_) { user = null; }
    if (user) { show('authChecking'); await adminCheck(user); return; }
    show('authSignIn');
    const params = new URLSearchParams(location.search);
    if (params.get('denied')) error('This account is not a Chatbot administrator.');
    const code = params.get('auth_error');
    if (code) {
      error({ cancelled: 'Microsoft sign-in was cancelled.', disabled: 'This account has been disabled. Contact an AI4DRR Chatbot administrator.' }[code]
        || 'Microsoft sign-in could not be completed. Please try again.');
      params.delete('auth_error');
      history.replaceState(null, '', location.pathname + (params.toString() ? '?' + params.toString() : ''));
    }
  }

  $('ssoBtn').addEventListener('click', (e) => {
    if ($('ssoBtn').getAttribute('aria-disabled') === 'true') e.preventDefault();
    // otherwise the anchor navigates to /api/auth/entra/login?next=/admin/login
  });

  $('localForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    error('');
    const f = $('localForm');
    if (!f.email.value.trim() || !f.password.value) { error('Enter your email address and password.'); return; }
    setBusy($('localSubmit'), true);
    try {
      const user = await IDENTITY.localLogin({ email: f.email.value.trim(), password: f.password.value });
      f.password.value = '';
      show('authChecking');
      await adminCheck(user);
    } catch (err) { error(err.detail || err.message); }
    finally { setBusy($('localSubmit'), false); }
  });

  $('deniedSignOut').addEventListener('click', async (e) => {
    e.preventDefault();
    let out = null;
    try { out = await IDENTITY.signOut(); } catch (_) { /* cookie is invalid either way */ }
    if (out && out.redirect) { window.location.assign(out.redirect); return; }
    show('authSignIn');
  });

  init();
})();
