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
  getArticles: (source = 'all', page = 1, pageSize = 20, sortBy = 'time') =>
    request(`/articles?source=${source}&page=${page}&page_size=${pageSize}&sort_by=${sortBy}`),
  getArticle: (id) => request(`/articles/${encodeURIComponent(id)}`),
  createArticle: (data) => request('/articles', { method: 'POST', body: JSON.stringify(data) }),
  updateArticle: (id, data) => request(`/articles/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteArticle: (id) => request(`/articles/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  // Pipeline
  run: (source = 'all', dryRun = false) => request('/run', {
    method: 'POST', body: JSON.stringify({ source, dry_run: dryRun }),
  }),
  refetch: (source, stcnUrls = [], techflowIds = [], blockbeatsUrls = [], chaincatcherUrls = [], odailyUrls = []) => request('/refetch', {
    method: 'POST', body: JSON.stringify({ source, stcn_urls: stcnUrls, techflow_ids: techflowIds, blockbeats_urls: blockbeatsUrls, chaincatcher_urls: chaincatcherUrls, odaily_urls: odailyUrls }),
  }),
  cancelRun: () => request('/cancel', { method: 'POST' }),
  forceReset: () => request('/force-reset', { method: 'POST' }),

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
  testLlmTask: (task) => request(`/settings/test-llm/${task}`, { method: 'POST' }),
  getLlmTasks: () => request('/settings/llm-tasks'),

  // AI Edit
  aiEditArticle: (id, systemPrompt, userPrompt) => request(`/articles/${encodeURIComponent(id)}/ai-edit`, {
    method: 'POST', body: JSON.stringify({ system_prompt: systemPrompt, user_prompt: userPrompt }),
  }),
  saveArticleDraft: (id) => request(`/articles/${encodeURIComponent(id)}/draft`, { method: 'POST' }),
  publishArticle: (id) => request(`/articles/${encodeURIComponent(id)}/publish`, { method: 'POST' }),
  broadcastArticle: (id) => request(`/articles/${encodeURIComponent(id)}/broadcast`, { method: 'POST' }),
  republishArticle: (id) => request(`/articles/${encodeURIComponent(id)}/republish`, { method: 'POST' }),
  getProfile: () => request('/auth/profile'),
  updateProfile: (username) => request('/auth/profile', {
    method: 'PUT', body: JSON.stringify({ username }),
  }),
  changePassword: (oldPassword, newPassword) => request('/auth/change-password', {
    method: 'POST', body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  }),

  // Batch operations
  batchDeleteArticles: (ids) => request('/articles/batch-delete', {
    method: 'POST', body: JSON.stringify({ ids }),
  }),

  // AI Articles
  getAiArticles: (params = {}) => {
    const qs = new URLSearchParams()
    if (params.source && params.source !== 'all') qs.set('source', params.source)
    if (params.category) qs.set('category', params.category)
    if (params.minScore) qs.set('min_score', params.minScore)
    if (params.tag) qs.set('tag', params.tag)
    if (params.page) qs.set('page', params.page)
    if (params.pageSize) qs.set('page_size', params.pageSize)
    qs.set('sort_by', params.sortBy || 'time')
    return request(`/ai/articles?${qs.toString()}`)
  },
  getAiArticle: (id) => request(`/ai/articles/${encodeURIComponent(id)}`),
  updateAiArticle: (id, data) => request(`/ai/articles/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteAiArticle: (id) => request(`/ai/articles/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  batchDeleteAiArticles: (ids) => request('/ai/articles/batch-delete', {
    method: 'POST', body: JSON.stringify({ ids }),
  }),
  aiEditAiArticle: (id, systemPrompt, userPrompt) => request(`/ai/articles/${encodeURIComponent(id)}/ai-edit`, {
    method: 'POST', body: JSON.stringify({ system_prompt: systemPrompt, user_prompt: userPrompt }),
  }),
  saveAiArticleDraft: (id) => request(`/ai/articles/${encodeURIComponent(id)}/draft`, { method: 'POST' }),
  ingestAiArticles: () => request('/ai/ingest', { method: 'POST' }),
  publishAiArticle: (id) => request(`/ai/articles/${encodeURIComponent(id)}/publish`, { method: 'POST' }),
  getAiTags: () => request('/ai/tags'),
  getAiStats: () => request('/ai/stats'),

  // AI Pipeline control
  getAiStatus: () => request('/ai/status'),
  runAiIngest: (source = 'all') => request('/ai/run', {
    method: 'POST', body: JSON.stringify({ source }),
  }),
  cancelAiRun: () => request('/ai/cancel', { method: 'POST' }),
  getAiSchedules: () => request('/ai/schedules'),
  updateAiSchedule: (sourceKey, enabled, intervalMinutes) => request(`/ai/schedules/${sourceKey}`, {
    method: 'PUT', body: JSON.stringify({ enabled, interval_minutes: intervalMinutes }),
  }),

  // Workflow
  getWorkflowStatus: () => request('/workflow/status'),
  runWorkflowPushCheck: () => request('/workflow/push-check', { method: 'POST' }),
  runWorkflowBroadcastCheck: () => request('/workflow/broadcast-check', { method: 'POST' }),
  getBlocklist: () => request('/blocklist'),
  createBlocklistRule: (data) => request('/blocklist', { method: 'POST', body: JSON.stringify(data) }),
  updateBlocklistRule: (id, data) => request(`/blocklist/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteBlocklistRule: (id) => request(`/blocklist/${id}`, { method: 'DELETE' }),
};
