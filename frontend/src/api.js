const API_BASE = '/api';

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  getStatus: () => request('/status'),
  getState: () => request('/state'),
  getArticles: (source = 'all', page = 1, pageSize = 20) =>
    request(`/articles?source=${source}&page=${page}&page_size=${pageSize}`),
  getArticle: (id) => request(`/articles/${encodeURIComponent(id)}`),
  getLogs: (lines = 200) => request(`/logs?lines=${lines}`),
  run: (source = 'all', dryRun = false) => request('/run', {
    method: 'POST',
    body: JSON.stringify({ source, dry_run: dryRun }),
  }),
  refetch: (source, stcnUrls = [], techflowIds = [], blockbeatsUrls = [], chaincatcherUrls = []) => request('/refetch', {
    method: 'POST',
    body: JSON.stringify({ source, stcn_urls: stcnUrls, techflow_ids: techflowIds, blockbeats_urls: blockbeatsUrls, chaincatcher_urls: chaincatcherUrls }),
  }),
  removeFromState: (id) => request(`/state/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  createArticle: (data) => request('/articles', { method: 'POST', body: JSON.stringify(data) }),
  updateArticle: (id, data) => request(`/articles/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteArticle: (id) => request(`/articles/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  getSchedules: () => request('/schedules'),
  updateSchedule: (sourceKey, enabled, intervalMinutes) => request(`/schedules/${sourceKey}`, {
    method: 'PUT',
    body: JSON.stringify({ enabled, interval_minutes: intervalMinutes }),
  }),
};
