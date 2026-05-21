// Lightweight API client for the FastAPI backend.
// Token is stored in localStorage and added to every request.

const isBrowser = typeof window !== 'undefined';

export const TOKEN_KEY = 'sv_token';
export const USER_KEY = 'sv_user';

export function getToken(): string | null {
  if (!isBrowser) return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(t: string) {
  if (isBrowser) localStorage.setItem(TOKEN_KEY, t);
}

export function clearToken() {
  if (isBrowser) {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  }
}

export function getUser(): any | null {
  if (!isBrowser) return null;
  const raw = localStorage.getItem(USER_KEY);
  return raw ? JSON.parse(raw) : null;
}

export function setUser(u: any) {
  if (isBrowser) localStorage.setItem(USER_KEY, JSON.stringify(u));
}

const API_BASE = '/api';   // Next rewrites /api/* -> backend

async function request<T = any>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string>) || {}),
  };
  const tok = getToken();
  if (tok) headers['Authorization'] = `Bearer ${tok}`;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (res.status === 401) {
    clearToken();
    if (isBrowser && !window.location.pathname.startsWith('/login')) {
      window.location.href = '/login';
    }
    throw new Error('Unauthorized');
  }
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      msg = j.detail || JSON.stringify(j);
    } catch {}
    throw new Error(msg);
  }
  if (res.status === 204) return undefined as any;
  return res.json();
}

async function upload<T = any>(path: string, file: File): Promise<T> {
  const fd = new FormData();
  fd.append('file', file);
  const headers: Record<string, string> = {};
  const tok = getToken();
  if (tok) headers['Authorization'] = `Bearer ${tok}`;
  const res = await fetch(`${API_BASE}${path}`, { method: 'POST', headers, body: fd });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  // auth
  register: (payload: any) =>
    request('/auth/register', { method: 'POST', body: JSON.stringify(payload) }),
  login: (payload: any) =>
    request('/auth/login', { method: 'POST', body: JSON.stringify(payload) }),
  me: () => request('/auth/me'),

  // users
  listUsers: () => request('/users'),
  lookupUser: (username: string) =>
    request(`/users/lookup?username=${encodeURIComponent(username)}`),
  setUserActive: (id: number, active: boolean) =>
    request(`/users/${id}/active?active=${active}`, { method: 'PATCH' }),
  setUserAdmin: (id: number, admin: boolean) =>
    request(`/users/${id}/admin?admin=${admin}`, { method: 'PATCH' }),

  // projects
  listProjects: () => request('/projects'),
  createProject: (p: any) =>
    request('/projects', { method: 'POST', body: JSON.stringify(p) }),
  getProject: (id: number) => request(`/projects/${id}`),
  updateProject: (id: number, p: any) =>
    request(`/projects/${id}`, { method: 'PATCH', body: JSON.stringify(p) }),
  deleteProject: (id: number) =>
    request(`/projects/${id}`, { method: 'DELETE' }),
  listMembers: (id: number) => request(`/projects/${id}/members`),
  addMember: (id: number, m: any) =>
    request(`/projects/${id}/members`, { method: 'POST', body: JSON.stringify(m) }),
  removeMember: (id: number, uid: number) =>
    request(`/projects/${id}/members/${uid}`, { method: 'DELETE' }),
  uploadSource: (id: number, file: File) =>
    upload(`/projects/${id}/source`, file),
  listEpisodes: (id: number) => request(`/projects/${id}/episodes`),

  // tasks
  listTasks: (q: { project_id?: number; status?: string; assigned_to_me?: boolean } = {}) => {
    const sp = new URLSearchParams();
    if (q.project_id) sp.set('project_id', String(q.project_id));
    if (q.status) sp.set('status', q.status);
    if (q.assigned_to_me) sp.set('assigned_to_me', 'true');
    const qs = sp.toString();
    return request(`/tasks${qs ? '?' + qs : ''}`);
  },
  createTask: (t: any) =>
    request('/tasks', { method: 'POST', body: JSON.stringify(t) }),
  retryTask: (id: number) =>
    request(`/tasks/${id}/retry`, { method: 'POST' }),
  patchTask: (id: number, p: any) =>
    request(`/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(p) }),

  // episodes
  getEpisode: (id: number) => request(`/episodes/${id}`),
  listScripts: (epId: number) => request(`/episodes/${epId}/scripts`),
  listStoryboards: (epId: number) => request(`/episodes/${epId}/storyboards`),
  createStoryboard: (epId: number, sb: any) =>
    request(`/episodes/${epId}/storyboards`, { method: 'POST', body: JSON.stringify(sb) }),

  // reviews
  reviewTask: (taskId: number, action: string, note?: string) =>
    request(`/reviews/tasks/${taskId}`, {
      method: 'POST',
      body: JSON.stringify({ action, note }),
    }),

  // workspace
  workspace: () => request('/workspace'),

  // assets / accounts / schedules
  listAssets: (project_id?: number) =>
    request(`/assets${project_id ? '?project_id=' + project_id : ''}`),
  /** Auth-checked download URL. Use as href in <a download> — browser
   *  will send the cookie/header? No: we use Authorization header so a
   *  plain <a> link won't authenticate. For programmatic download use
   *  request() to get a Blob, or have the user click a button that fetches
   *  with auth and then triggers a save dialog. */
  assetDownloadPath: (asset_id: number) => `/api/assets/${asset_id}/download`,
  listAccounts: () => request('/accounts'),
  listUsableAccounts: (project_id: number) =>
    request(`/accounts/usable?project_id=${project_id}`),
  createAccount: (a: any) =>
    request('/accounts', { method: 'POST', body: JSON.stringify(a) }),
  deleteAccount: (id: number) =>
    request(`/accounts/${id}`, { method: 'DELETE' }),
  listSchedules: (status?: string) =>
    request(`/schedules${status ? '?status=' + status : ''}`),
  createSchedule: (s: any) =>
    request('/schedules', { method: 'POST', body: JSON.stringify(s) }),
  updateScheduleStatus: (id: number, status: string) =>
    request(`/schedules/${id}/status?status=${status}`, { method: 'PATCH' }),

  // audit logs
  listAuditLogs: (q: {
    action?: string;
    project_id?: number;
    user_id?: number;
    since?: string;
    until?: string;
    limit?: number;
    offset?: number;
  } = {}) => {
    const sp = new URLSearchParams();
    Object.entries(q).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') sp.append(k, String(v));
    });
    const qs = sp.toString();
    return request(`/audit-logs${qs ? '?' + qs : ''}`);
  },

  // system
  systemHealth: () => request('/system/health-full'),
  permissionMatrix: () => request('/system/permission-matrix'),
};
