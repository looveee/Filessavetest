'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { PageHeader, EmptyState } from '@/components/UI';

const PLATFORMS = ['douyin', 'xiaohongshu', 'bilibili', 'kuaishou', 'wechat_video', 'youtube'];
const FREQS = ['daily', 'weekly_3', 'weekly_2', 'weekly', 'custom'];

export default function AccountsPage() {
  const [list, setList] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [form, setForm] = useState({
    platform: 'douyin',
    name: '',
    persona: '',
    domain: '',
    publish_frequency: 'daily',
  });

  async function load() {
    setLoading(true);
    try {
      const r = await api.listAccounts();
      setList(r);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  function flash(m: string) {
    setMsg(m);
    setTimeout(() => setMsg(''), 2500);
  }

  async function create() {
    if (!form.name) {
      flash('请填写账号名');
      return;
    }
    setBusy(true);
    try {
      await api.createAccount(form);
      setShow(false);
      setForm({ platform: 'douyin', name: '', persona: '', domain: '', publish_frequency: 'daily' });
      flash('已创建');
      load();
    } catch (e: any) {
      flash(e.message || '失败');
    } finally {
      setBusy(false);
    }
  }

  async function del(id: number) {
    if (!confirm('确定删除该账号？相关排期不会被删除。')) return;
    try {
      await api.deleteAccount(id);
      flash('已删除');
      load();
    } catch (e: any) {
      flash(e.message || '失败');
    }
  }

  return (
    <div className="space-y-8">
      <PageHeader
        title="账号管理 / Accounts"
        subtitle="管理你的发布矩阵：每个账号的人设、领域、发布节奏。"
        right={
          <button className="btn btn-primary" onClick={() => setShow(true)}>
            + 新增账号
          </button>
        }
      />

      {msg && (
        <div className="card border-accent bg-accent/10 font-mono text-sm">{msg}</div>
      )}

      {loading ? (
        <div className="card">加载中…</div>
      ) : list.length === 0 ? (
        <EmptyState title="还没有账号" desc="点击右上角“+ 新增账号”，添加你的第一个发布账号。" />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {list.map((a) => (
            <div key={a.id} className="card space-y-2">
              <div className="flex items-start justify-between">
                <div>
                  <div className="text-xs font-mono uppercase text-ink-500">{a.platform}</div>
                  <h3 className="font-display text-xl">{a.name}</h3>
                </div>
                <button
                  className="text-xs underline text-ink-500 hover:text-accent"
                  onClick={() => del(a.id)}
                >
                  删除
                </button>
              </div>
              {a.persona && (
                <div className="text-sm">
                  <span className="text-ink-500 font-mono text-xs">人设：</span>
                  {a.persona}
                </div>
              )}
              {a.domain && (
                <div className="text-sm">
                  <span className="text-ink-500 font-mono text-xs">领域：</span>
                  {a.domain}
                </div>
              )}
              <div className="text-xs font-mono text-ink-500">
                节奏：{a.publish_frequency}
              </div>
            </div>
          ))}
        </div>
      )}

      {show && (
        <div className="fixed inset-0 bg-ink-900/50 flex items-center justify-center p-4 z-50">
          <div className="card bg-white max-w-lg w-full space-y-4">
            <h3 className="font-display text-2xl">新增账号</h3>
            <div className="space-y-3">
              <div>
                <label className="text-xs font-mono text-ink-500">平台</label>
                <select
                  className="input"
                  value={form.platform}
                  onChange={(e) => setForm({ ...form, platform: e.target.value })}
                >
                  {PLATFORMS.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs font-mono text-ink-500">账号名 *</label>
                <input
                  className="input"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </div>
              <div>
                <label className="text-xs font-mono text-ink-500">人设</label>
                <input
                  className="input"
                  placeholder="如：90 后历史科普 UP 主"
                  value={form.persona}
                  onChange={(e) => setForm({ ...form, persona: e.target.value })}
                />
              </div>
              <div>
                <label className="text-xs font-mono text-ink-500">领域</label>
                <input
                  className="input"
                  placeholder="如：历史 / 悬疑 / 短剧"
                  value={form.domain}
                  onChange={(e) => setForm({ ...form, domain: e.target.value })}
                />
              </div>
              <div>
                <label className="text-xs font-mono text-ink-500">发布频率</label>
                <select
                  className="input"
                  value={form.publish_frequency}
                  onChange={(e) => setForm({ ...form, publish_frequency: e.target.value })}
                >
                  {FREQS.map((f) => (
                    <option key={f} value={f}>
                      {f}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="flex gap-2 justify-end">
              <button className="btn btn-ghost" onClick={() => setShow(false)}>
                取消
              </button>
              <button className="btn btn-primary" disabled={busy} onClick={create}>
                {busy ? '...' : '创建'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
