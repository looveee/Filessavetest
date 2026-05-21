'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { PageHeader, EmptyState } from '@/components/UI';

const ACTIONS = [
  '', 'user.login', 'user.login_failed', 'user.register',
  'project.create', 'project.update', 'project.delete',
  'member.add', 'member.role_update', 'member.remove',
  'source.upload',
  'task.create', 'task.assign', 'task.retry', 'task.update',
  'review.approve', 'review.reject', 'review.rewrite',
  'review.enhance_conflict', 'review.enhance_cliffhanger',
  'review.enhance_hook', 'review.redo',
  'storyboard.create', 'storyboard.update', 'storyboard.delete',
  'account.create', 'account.delete',
  'schedule.create', 'schedule.status_update',
];

function shortJson(v: any): string {
  if (v === null || v === undefined) return '';
  try {
    const s = typeof v === 'string' ? v : JSON.stringify(v);
    return s.length > 200 ? s.slice(0, 200) + '…' : s;
  } catch {
    return String(v);
  }
}

export default function AuditPage() {
  const [logs, setLogs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [filters, setFilters] = useState({
    action: '',
    project_id: '',
    user_id: '',
    since: '',
    until: '',
  });
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

  async function load() {
    setLoading(true);
    setErr('');
    try {
      const q: any = { limit: 200 };
      if (filters.action) q.action = filters.action;
      if (filters.project_id) q.project_id = Number(filters.project_id);
      if (filters.user_id) q.user_id = Number(filters.user_id);
      if (filters.since) q.since = new Date(filters.since).toISOString();
      if (filters.until) q.until = new Date(filters.until).toISOString();
      const r = await api.listAuditLogs(q);
      setLogs(r);
    } catch (e: any) {
      setErr(e.message || '加载失败');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="space-y-8">
      <PageHeader
        title="审计日志 / Audit Logs"
        subtitle="所有关键操作的不可变记录。admin 可看全部；普通用户只看自己参与的项目。"
        right={
          <button className="btn btn-dark" onClick={load}>
            刷新
          </button>
        }
      />

      <div className="card grid grid-cols-1 md:grid-cols-5 gap-3">
        <div>
          <label className="text-xs font-mono text-ink-500">action</label>
          <select
            className="input"
            value={filters.action}
            onChange={(e) => setFilters({ ...filters, action: e.target.value })}
          >
            {ACTIONS.map((a) => (
              <option key={a} value={a}>
                {a || '(全部)'}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs font-mono text-ink-500">project_id</label>
          <input
            className="input"
            type="number"
            value={filters.project_id}
            onChange={(e) => setFilters({ ...filters, project_id: e.target.value })}
          />
        </div>
        <div>
          <label className="text-xs font-mono text-ink-500">user_id</label>
          <input
            className="input"
            type="number"
            value={filters.user_id}
            onChange={(e) => setFilters({ ...filters, user_id: e.target.value })}
          />
        </div>
        <div>
          <label className="text-xs font-mono text-ink-500">since</label>
          <input
            className="input"
            type="datetime-local"
            value={filters.since}
            onChange={(e) => setFilters({ ...filters, since: e.target.value })}
          />
        </div>
        <div>
          <label className="text-xs font-mono text-ink-500">until</label>
          <input
            className="input"
            type="datetime-local"
            value={filters.until}
            onChange={(e) => setFilters({ ...filters, until: e.target.value })}
          />
        </div>
        <div className="md:col-span-5 flex justify-end gap-2">
          <button
            className="btn btn-ghost"
            onClick={() =>
              setFilters({ action: '', project_id: '', user_id: '', since: '', until: '' })
            }
          >
            清空
          </button>
          <button className="btn btn-primary" onClick={load}>
            查询
          </button>
        </div>
      </div>

      {err && <div className="card border-accent text-accent">{err}</div>}

      {loading ? (
        <div className="card">加载中…</div>
      ) : logs.length === 0 ? (
        <EmptyState title="无匹配日志" desc="调整筛选条件再试一次。" />
      ) : (
        <div className="card p-0 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-ink-100 border-b-2 border-ink-900">
              <tr className="text-left font-mono text-xs uppercase">
                <th className="p-2">id</th>
                <th className="p-2">time</th>
                <th className="p-2">user</th>
                <th className="p-2">action</th>
                <th className="p-2">project</th>
                <th className="p-2">task</th>
                <th className="p-2">target</th>
                <th className="p-2">ip</th>
                <th className="p-2"></th>
              </tr>
            </thead>
            <tbody>
              {logs.map((l) => {
                const open = !!expanded[l.id];
                return (
                  <>
                    <tr key={l.id} className="border-b border-ink-200 hover:bg-yellow-50">
                      <td className="p-2 font-mono text-xs">#{l.id}</td>
                      <td className="p-2 font-mono text-xs whitespace-nowrap">
                        {new Date(l.created_at).toLocaleString('zh-CN')}
                      </td>
                      <td className="p-2 font-mono text-xs">
                        {l.user_id ?? '—'}
                      </td>
                      <td className="p-2">
                        <span className="badge bg-ink-100 border border-ink-900 font-mono text-xs">
                          {l.action}
                        </span>
                      </td>
                      <td className="p-2 font-mono text-xs">
                        {l.project_id ?? '—'}
                      </td>
                      <td className="p-2 font-mono text-xs">
                        {l.task_id ?? '—'}
                      </td>
                      <td className="p-2 font-mono text-xs">
                        {l.target_type ? `${l.target_type}#${l.target_id ?? ''}` : '—'}
                      </td>
                      <td className="p-2 font-mono text-xs">{l.ip || '—'}</td>
                      <td className="p-2">
                        <button
                          className="text-xs underline"
                          onClick={() =>
                            setExpanded((s) => ({ ...s, [l.id]: !s[l.id] }))
                          }
                        >
                          {open ? '收起' : '详情'}
                        </button>
                      </td>
                    </tr>
                    {open && (
                      <tr className="bg-ink-50 border-b border-ink-200">
                        <td className="p-3" colSpan={9}>
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 font-mono text-xs">
                            <div>
                              <div className="text-ink-500 mb-1">before_value</div>
                              <pre className="whitespace-pre-wrap break-all">
                                {shortJson(l.before_value) || '—'}
                              </pre>
                            </div>
                            <div>
                              <div className="text-ink-500 mb-1">after_value</div>
                              <pre className="whitespace-pre-wrap break-all">
                                {shortJson(l.after_value) || '—'}
                              </pre>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
