const API_BASE = '/api';

function getToken() {
  try { return localStorage.getItem('auth_token') || '' } catch { return '' }
}

export function setToken(token) {
  try { localStorage.setItem('auth_token', token) } catch {}
}

export function setRole(role) {
  try { localStorage.setItem('auth_role', role || 'admin') } catch {}
}

export function getRole() {
  try { return localStorage.getItem('auth_role') || 'admin' } catch { return 'admin' }
}

export function clearToken() {
  try { localStorage.removeItem('auth_token'); localStorage.removeItem('auth_role') } catch {}
}

async function request(path, options = {}) {
  const token = getToken();
  const headers = { 'Content-Type': 'application/json', ...options.headers };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });

  if (res.status === 401) {
    clearToken();
    window.dispatchEvent(new Event('auth:logout'));
    throw new Error('认证已过期，请重新登录');
  }

  if (res.status === 403) {
    throw new Error('访客无操作权限');
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  // Auth
  login: (username, password) => request('/auth/login', {
    method: 'POST', body: JSON.stringify({ username, password }),
  }),
  checkAuth: () => request('/auth/check'),

  // Status
  getStatus: () => request('/status'),
  getState: () => request('/state'),

  // Articles
  getArticles: (source = 'all', page = 1, pageSize = 20) =>
    request(`/articles?source=${source}&page=${page}&page_size=${pageSize}`),
  getArticle: (id) => request(`/articles/${encodeURIComponent(id)}`),
  createArticle: (data) => request('/articles', { method: 'POST', body: JSON.stringify(data) }),
  updateArticle: (id, data) => request(`/articles/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteArticle: (id) => request(`/articles/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  // Pipeline
  run: (source = 'all', dryRun = false) => request('/run', {
    method: 'POST', body: JSON.stringify({ source, dry_run: dryRun }),
  }),
  refetch: (source, stcnUrls = [], techflowIds = [], blockbeatsUrls = [], chaincatcherUrls = []) => request('/refetch', {
    method: 'POST', body: JSON.stringify({ source, stcn_urls: stcnUrls, techflow_ids: techflowIds, blockbeats_urls: blockbeatsUrls, chaincatcher_urls: chaincatcherUrls }),
  }),

  // Logs
  getLogs: (lines = 200) => request(`/logs?lines=${lines}`),

  // State
  removeFromState: (id) => request(`/state/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  // Scheduler
  getSchedules: () => request('/schedules'),
  updateSchedule: (sourceKey, enabled, intervalMinutes) => request(`/schedules/${sourceKey}`, {
    method: 'PUT', body: JSON.stringify({ enabled, interval_minutes: intervalMinutes }),
  }),

  // Settings
  getSettings: () => request('/settings'),
  updateSettings: (settings) => request('/settings', {
    method: 'PUT', body: JSON.stringify({ settings }),
  }),
  testLlm: () => request('/settings/test-llm', { method: 'POST' }),
  getProfile: () => request('/auth/profile'),
  updateProfile: (username) => request('/auth/profile', {
    method: 'PUT', body: JSON.stringify({ username }),
  }),
  changePassword: (oldPassword, newPassword) => request('/auth/change-password', {
    method: 'POST', body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  }),
};
