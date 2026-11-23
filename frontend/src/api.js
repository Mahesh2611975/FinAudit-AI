const API_BASE = (import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000')
  .trim()
  .replace(/\/+$/, '')
  .replace(/\/api$/, '');

const TOKEN_KEY = 'finaudit.auth.token';
const USER_KEY = 'finaudit.auth.user';
const EXPIRES_AT_KEY = 'finaudit.auth.expiresAt';

export const API = API_BASE;

// Explicit opt-in only. The backend remains authoritative: this flag changes
// navigation, but a real backend-issued demo token is still required for data.
const DEMO_TRUTHY = ['1', 'true', 'yes', 'on'];
export const LOCAL_DEMO_MODE = DEMO_TRUTHY.includes(
  String(import.meta.env.VITE_LOCAL_DEMO_MODE || '').trim().toLowerCase()
);

export function getAuthToken() {
  try {
    const token = localStorage.getItem(TOKEN_KEY);
    const expiresAt = Number(localStorage.getItem(EXPIRES_AT_KEY) || 0);
    if (!token) return null;
    if (expiresAt && Date.now() >= expiresAt) {
      clearAuth();
      return null;
    }
    return token;
  } catch {
    return null;
  }
}

export function getAuthUser() {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function setAuth({ access_token, expires_in_minutes, user }) {
  localStorage.setItem(TOKEN_KEY, access_token);
  if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
  if (expires_in_minutes) {
    const expiresAt = Date.now() + Number(expires_in_minutes) * 60 * 1000;
    localStorage.setItem(EXPIRES_AT_KEY, String(expiresAt));
  } else {
    localStorage.removeItem(EXPIRES_AT_KEY);
  }
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  localStorage.removeItem(EXPIRES_AT_KEY);
}

export function isAuthenticated() {
  return Boolean(getAuthToken());
}

export async function getDemoStatus() {
  let response;
  try {
    response = await fetch(`${API_BASE}/api/auth/demo/status`, { cache: 'no-store' });
  } catch (err) {
    throw apiUnreachableError(err);
  }
  if (!response.ok) return { enabled: false };
  return response.json().catch(() => ({ enabled: false }));
}

export async function requestDemoSession() {
  let response;
  try {
    response = await fetch(`${API_BASE}/api/auth/demo/session`, { method: 'POST' });
  } catch (err) {
    throw apiUnreachableError(err);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(apiErrorMessage(data, '', response));
  return data;
}

function notifyAuthExpired() {
  clearAuth();
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('finaudit:auth-expired'));
  }
}

function apiUnreachableError(cause) {
  // A bare browser "Failed to fetch" hides the real cause (backend stopped,
  // wrong VITE_API_URL, or CORS). Surface the configured URL and next steps.
  const error = new Error(
    `Cannot reach the FinAudit API at ${API_BASE}. Start the backend (see README / DEPLOYMENT.md), ` +
    'then confirm VITE_API_URL points at it and the origin is allowed by FINAUDIT_CORS_ORIGINS.'
  );
  error.isNetworkError = true;
  error.cause = cause;
  return error;
}

export async function apiFetch(path, options = {}) {
  const token = getAuthToken();
  const headers = new Headers(options.headers || {});
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  } catch (err) {
    throw apiUnreachableError(err);
  }
  if (response.status === 401) {
    // Don't treat login/registration failures as expired-session redirect loops.
    const isAuthRequest = String(path).includes('/api/auth/');
    if (!isAuthRequest) notifyAuthExpired();
  }
  return response;
}

function apiErrorMessage(data, raw, response) {
  const detail = Array.isArray(data?.detail)
    ? data.detail.map((item) => (typeof item === 'string' ? item : item?.msg || JSON.stringify(item))).join(' ')
    : data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (detail?.message) return detail.message;
  const rawText = String(raw || '').trim();
  if (rawText) return rawText.slice(0, 400);
  return `Request failed (HTTP ${response.status} ${response.statusText || ''}).`.trim();
}

export async function apiJson(path, options = {}) {
  const response = await apiFetch(path, options);
  const raw = await response.text().catch(() => '');
  let data = {};
  if (raw) {
    try {
      data = JSON.parse(raw);
    } catch {
      // Non-JSON response (for example an HTML proxy error). Keep the raw text
      // so the user sees the real problem instead of a generic message.
    }
  }
  if (!response.ok) {
    const error = new Error(apiErrorMessage(data, raw, response));
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

export async function downloadApi(path, filename) {
  const response = await apiFetch(path);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail = Array.isArray(data?.detail) ? data.detail.join(' ') : data?.detail;
    throw new Error(typeof detail === 'string' ? detail : 'Download failed.');
  }
  const blob = await response.blob();
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename || path.split('/').pop() || 'download';
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}
