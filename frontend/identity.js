/* Identity for the frontend — server-side sessions.
 *
 * The browser holds an HttpOnly session cookie set by the API; this module
 * never sees a token and never stores identity in localStorage. Who is signed
 * in is whatever GET /api/auth/me says.
 *
 *   config()          → {entra_enabled, local_auth_enabled}  (public)
 *   current()         → user | null                          (GET /api/auth/me)
 *   entraLoginUrl()   → full-page redirect target for "Sign in with Microsoft"
 *   localLogin(f)     → user   (POST /api/auth/local/login;  only when local auth is enabled)
 *   localForgot(f)    → {detail} generic message (POST /api/auth/local/forgot; the e-mail carries a
 *                       one-time link to /reset-password — the account is never confirmed or denied)
 *   signOut()         → void   (POST /api/auth/logout)
 *
 * app.js depends only on these calls and the returned user
 * {id, email, name, first_name, last_name, department, unit}. */
(function () {
  'use strict';

  const api = () => window.AI4DRR.API_BASE;

  class AuthError extends Error {
    constructor(status, detail) { super(detail); this.status = status; this.detail = detail; }
  }

  async function call(method, path, body) {
    let resp;
    try {
      resp = await fetch(api() + path, {
        method,
        credentials: 'same-origin',
        headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (_) {
      throw new AuthError(0, 'The server could not be reached.');
    }
    if (resp.status === 204) return null;
    let data = null;
    try { data = await resp.json(); } catch (_) { data = null; }
    if (!resp.ok) {
      const d = data && data.detail;
      const detail = typeof d === 'string' ? d : Array.isArray(d) ? 'Please check the form and try again.' : `HTTP ${resp.status}`;
      throw new AuthError(resp.status, detail);
    }
    return data;
  }

  window.AI4DRR_IDENTITY = {
    AuthError,
    config: () => call('GET', '/auth/config'),
    current: async () => {
      try { return await call('GET', '/auth/me'); } catch (err) {
        if (err.status === 401) return null;
        throw err;
      }
    },
    entraLoginUrl: () => api() + '/auth/entra/login',
    localLogin: (f) => call('POST', '/auth/local/login', { email: f.email, password: f.password }),
    // No self-registration: accounts are created through the administrators' invitation flow.
    localForgot: (f) => call('POST', '/auth/local/forgot', { email: f.email }),
    // → {signed_out, redirect}: redirect is Microsoft's end-session URL for a B2C session, else null
    signOut: () => call('POST', '/auth/logout'),
  };
})();
