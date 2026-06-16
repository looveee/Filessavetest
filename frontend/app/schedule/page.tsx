'use client';
import { Suspense, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { PageHeader, StatusBadge, EmptyState } from '@/components/UI';

const STATUS_TABS = ['pending', 'published', 'failed', 'skipped'] as const;

function SchedulePageInner() {
  const sp = useSearchParams();
  const presetAsset = sp?.get('asset_id');

  const [schedules, setSchedules] = useState<any[]>([]);
  const [assets, setAssets] = useState<any[]>([]);
  const [accounts, setAccounts] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [show, setShow] = useState(!!presetAsset);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [tab, setTab] = useState<(typeof STATUS_TABS)[number]>('pending');

  // default scheduled_at: tomorrow 09:00 local
  function defaultSchedTime() {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    d.setHours(9, 0, 0, 0);
    // datetime-local needs YYYY-MM-DDTHH:mm
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  const [form, setForm] = useState({
    asset_id: presetAsset ? Number(presetAsset) : 0,
    account_id: 0,
    scheduled_at: defaultSchedTime(),
    title: '',
    description: '',
    tags: '',
  });

  async function load() {
    setLoading(true);
    try {
      const [s, a] = await Promise.all([
        api.listSchedules(),
        api.listAssets(),
      ]);
      setSchedules(s);
      setAssets(a);
      // Initial accounts list = caller's own accounts (used as fallback
      // when no asset has been selected yet in the form).
      try {
        const ownAcc = await api.listAccounts();
        setAccounts(ownAcc);
      } catch (_) {
        setAccounts([]);
      }
    } finally {
      setLoading(false);
    }
  }

  // Whenever the user picks an asset in the form, refresh the dropdown
  // to include accounts they're allowed to use *in that asset's project*
  // (their own + project owner's). Falls back to own accounts on failure.
  useEffect(() => {
    if (!form.asset_id) return;
    const selectedAsset = assets.find((a: any) => a.id === form.asset_id);
    if (!selectedAsset?.project_id) return;
    api
      .listUsableAccounts(selectedAsset.project_id)
      .then((rows: any[]) => setAccounts(rows))
      .catch(() => {
        // If usable endpoint fails (e.g. lacking account.use), fall back
        // to own accounts so the publisher at least sees something.
        api.listAccounts().then((rows: any[]) => setAccounts(rows)).catch(() => {});
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.asset_id]);

  useEffect(() => {
    load();
  }, []);

  function flash(m: string) {
    setMsg(m);
    setTimeout(() => setMsg(''), 2500);
  }

  async function create() {
    if (!form.asset_id || !form.account_id) {
      flash('请选择成片和账号');
      return;
    }
    setBusy(true);
    try {
      await api.createSchedule({
        asset_id: form.asset_id,
        account_id: form.account_id,
        scheduled_at: new Date(form.scheduled_at).toISOString(),
        title: form.title || undefined,
        description: form.description || undefined,
        tags: form.tags
          ? form.tags.split(/[,，\s]+/).filter(Boolean)
          : undefined,
      });
      setShow(false);
      flash('已加入排期');
      load();
    } catch (e: any) {
      flash(e.message || '失败');
    } finally {
      setBusy(false);
    }
  }

  async function setStatus(id: number, status: string) {
    try {
      await api.updateScheduleStatus(id, status);
      flash(`已标记：${status}`);
      load();
    } catch (e: any) {
      flash(e.message || '失败');
    }
  }

  const filtered = schedules.filter((s) => s.status === tab);

  return (
    <div className="space-y-8">
      <PageHeader
        title="发布排期 / Schedule"
        subtitle="把成片绑到账号 + 时间，构建你的发布矩阵节奏。"
        right={
          <button className="btn btn-primary" onClick={() => setShow(true)}>
            + 新增排期
          </button>
        }
      />

      {msg && <div className="card border-accent bg-accent/10 font-mono text-sm">{msg}</div>}

      <div className="flex gap-2 border-b-2 border-ink-900">
        {STATUS_TABS.map((s) => (
          <button
            key={s}
            onClick={() => setTab(s)}
            className={`px-4 py-2 font-mono text-sm uppercase border-2 border-b-0 -mb-0.5 ${
              tab === s
                ? 'bg-ink-900 text-white border-ink-900'
                : 'border-transparent text-ink-500 hover:text-ink-900'
            }`}
          >
            {s} ({schedules.filter((x) => x.status === s).length})
          </button>
        ))}
      </div>

      {loading ? (
        <div className="card">加载中…</div>
      ) : filtered.length === 0 ? (
        <EmptyState title="该分类下暂无排期" desc="新增一条试试。" />
      ) : (
        <div className="space-y-3">
          {filtered.map((s) => (
            <div key={s.id} className="card flex items-start justify-between gap-4 flex-wrap">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <StatusBadge status={s.status} />
                  <span className="text-xs font-mono text-ink-500">#{s.id}</span>
                </div>
                <div className="font-display text-lg">
                  {s.title || `Asset #${s.asset_id}`}
                </div>
                <div className="text-sm text-ink-500 font-mono mt-1">
                  → {s.account_platform || '?'} / {s.account_name || `account #${s.account_id}`}
                </div>
                <div className="text-sm text-ink-500 font-mono">
                  ⏰ {new Date(s.scheduled_at).toLocaleString('zh-CN')}
                </div>
                {s.description && <div className="text-sm mt-2">{s.description}</div>}
                {s.tags && s.tags.length > 0 && (
                  <div className="flex gap-1 mt-2 flex-wrap">
                    {s.tags.map((t: string, i: number) => (
                      <span key={i} className="tag">
                        #{t}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              {s.status === 'pending' && (
                <div className="flex flex-col gap-2">
                  <button className="btn btn-lime text-xs" onClick={() => setStatus(s.id, 'published')}>
                    标记已发布
                  </button>
                  <button className="btn btn-dark text-xs" onClick={() => setStatus(s.id, 'failed')}>
                    标记失败
                  </button>
                  <button className="btn btn-ghost text-xs" onClick={() => setStatus(s.id, 'skipped')}>
                    跳过
                  </button>
                </div>
              )}
              {s.status !== 'pending' && (
                <button
                  className="btn btn-ghost text-xs"
                  onClick={() => setStatus(s.id, 'pending')}
                >
                  恢复待发布
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {show && (
        <div className="fixed inset-0 bg-ink-900/50 flex items-center justify-center p-4 z-50">
          <div className="card bg-white max-w-lg w-full space-y-4">
            <h3 className="font-display text-2xl">新增排期</h3>
            <div className="space-y-3">
              <div>
                <label className="text-xs font-mono text-ink-500">成片 *</label>
                <select
                  className="input"
                  value={form.asset_id}
                  onChange={(e) => setForm({ ...form, asset_id: Number(e.target.value) })}
                >
                  <option value={0}>选择成片…</option>
                  {assets.map((a) => (
                    <option key={a.id} value={a.id}>
                      #{a.id} · {a.title || a.file_path}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs font-mono text-ink-500">发布账号 *</label>
                <select
                  className="input"
                  value={form.account_id}
                  onChange={(e) => setForm({ ...form, account_id: Number(e.target.value) })}
                >
                  <option value={0}>选择账号…</option>
                  {accounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      [{a.platform}] {a.name}
                    </option>
                  ))}
                </select>
                {accounts.length === 0 && (
                  <div className="text-xs text-accent mt-1">
                    先去“账号管理”新增账号
                  </div>
                )}
              </div>
              <div>
                <label className="text-xs font-mono text-ink-500">发布时间 *</label>
                <input
                  type="datetime-local"
                  className="input"
                  value={form.scheduled_at}
                  onChange={(e) => setForm({ ...form, scheduled_at: e.target.value })}
                />
              </div>
              <div>
                <label className="text-xs font-mono text-ink-500">标题</label>
                <input
                  className="input"
                  value={form.title}
                  onChange={(e) => setForm({ ...form, title: e.target.value })}
                />
              </div>
              <div>
                <label className="text-xs font-mono text-ink-500">描述</label>
                <textarea
                  className="input min-h-[60px]"
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                />
              </div>
              <div>
                <label className="text-xs font-mono text-ink-500">标签（逗号分隔）</label>
                <input
                  className="input"
                  placeholder="历史, 短剧, 悬疑"
                  value={form.tags}
                  onChange={(e) => setForm({ ...form, tags: e.target.value })}
                />
              </div>
            </div>
            <div className="flex gap-2 justify-end">
              <button className="btn btn-ghost" onClick={() => setShow(false)}>
                取消
              </button>
              <button className="btn btn-primary" disabled={busy} onClick={create}>
                {busy ? '...' : '加入排期'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function SchedulePage() {
  return (
    <Suspense fallback={<div className="card">加载中…</div>}>
      <SchedulePageInner />
    </Suspense>
  );
}
