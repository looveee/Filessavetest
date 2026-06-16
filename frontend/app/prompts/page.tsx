'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { PageHeader, EmptyState } from '@/components/UI';

export default function PromptsPage() {
  const [items, setItems] = useState<any[]>([]);
  const [active, setActive] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [edit, setEdit] = useState(false);
  const [draft, setDraft] = useState<any>({});

  const load = async () => {
    setLoading(true);
    setErr('');
    try {
      const list = await api.listPrompts();
      setItems(list);
      if (active) {
        const found = list.find((x: any) => x.id === active.id);
        setActive(found || list[0] || null);
      }
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const flash = (s: string) => { setMsg(s); setTimeout(() => setMsg(''), 2500); };

  const open = (p: any) => {
    setActive(p);
    setEdit(false);
    setDraft({
      name: p.name,
      system_prompt: p.system_prompt,
      user_prompt_template: p.user_prompt_template,
      is_active: p.is_active,
    });
  };

  const save = async () => {
    try {
      await api.updatePrompt(active.id, draft);
      flash('已保存');
      setEdit(false);
      await load();
    } catch (e: any) { flash('失败：' + e.message); }
  };

  const clone = async () => {
    try {
      const c = await api.clonePrompt(active.id);
      flash(`已克隆为 v${c.version}（未激活）`);
      await load();
      open(c);
    } catch (e: any) { flash('失败：' + e.message); }
  };

  if (loading) return <div className="font-mono text-sm">loading…</div>;
  if (err) return <EmptyState title="无法加载" desc={err.includes('403') ? '仅管理员可访问 Prompt 模板。' : err} />;

  return (
    <>
      <PageHeader title="Prompt Templates" subtitle="AI 提示词模板 · 查看 / 编辑 / 克隆新版本" />
      {msg && <div className="mb-4 border-2 border-ink-900 bg-lime px-3 py-2 text-sm font-mono">{msg}</div>}

      <div className="grid md:grid-cols-3 gap-4">
        <div className="md:col-span-1 space-y-2 max-h-[75vh] overflow-auto pr-1">
          {items.length === 0 && <EmptyState>没有模板</EmptyState>}
          {items.map((p) => (
            <button
              key={p.id}
              onClick={() => open(p)}
              className={`block w-full text-left card hover:shadow-hard transition ${
                active?.id === p.id ? 'bg-lime' : ''
              }`}
            >
              <div className="flex justify-between items-center">
                <span className="font-mono text-sm">{p.key}</span>
                <span className="tag">v{p.version}</span>
              </div>
              <div className="text-xs text-ink-500 mt-1 flex items-center justify-between">
                <span>{p.name}</span>
                {p.is_active ? (
                  <span className="tag bg-lime">active</span>
                ) : (
                  <span className="tag bg-ink-200">inactive</span>
                )}
              </div>
            </button>
          ))}
        </div>

        <div className="md:col-span-2 card">
          {!active ? (
            <EmptyState>选择一个模板查看</EmptyState>
          ) : (
            <>
              <div className="flex justify-between items-start mb-4">
                <div>
                  <h3 className="font-display font-bold text-xl">
                    {active.key} <span className="tag">v{active.version}</span>
                  </h3>
                  <p className="text-xs text-ink-500 font-mono">id={active.id}</p>
                </div>
                <div className="flex gap-2">
                  {!edit && <button onClick={() => setEdit(true)} className="btn btn-ghost text-sm">编辑</button>}
                  <button onClick={clone} className="btn btn-dark text-sm">克隆新版本</button>
                </div>
              </div>

              {!edit ? (
                <div className="space-y-4 text-sm">
                  <Field label="名称" value={active.name} />
                  <Field label="System Prompt" value={active.system_prompt} pre />
                  <Field label="User Prompt Template" value={active.user_prompt_template} pre />
                  <div>
                    <div className="font-display font-bold mb-1">Output Schema</div>
                    <pre className="bg-ink-50 border-2 border-ink-200 p-2 text-xs overflow-auto max-h-40">
{JSON.stringify(active.output_schema, null, 2)}
                    </pre>
                  </div>
                </div>
              ) : (
                <div className="space-y-3 text-sm">
                  <label className="block">
                    <span className="font-display font-bold">名称</span>
                    <input className="input w-full mt-1" value={draft.name || ''}
                           onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
                  </label>
                  <label className="block">
                    <span className="font-display font-bold">System Prompt</span>
                    <textarea className="input w-full mt-1 h-28 font-mono text-xs"
                              value={draft.system_prompt || ''}
                              onChange={(e) => setDraft({ ...draft, system_prompt: e.target.value })} />
                  </label>
                  <label className="block">
                    <span className="font-display font-bold">User Prompt Template</span>
                    <textarea className="input w-full mt-1 h-48 font-mono text-xs"
                              value={draft.user_prompt_template || ''}
                              onChange={(e) => setDraft({ ...draft, user_prompt_template: e.target.value })} />
                  </label>
                  <label className="flex items-center gap-2">
                    <input type="checkbox" checked={!!draft.is_active}
                           onChange={(e) => setDraft({ ...draft, is_active: e.target.checked })} />
                    <span className="font-mono">激活 (active)</span>
                  </label>
                  <div className="flex gap-2">
                    <button onClick={save} className="btn btn-primary">保存</button>
                    <button onClick={() => { setEdit(false); open(active); }} className="btn btn-ghost">取消</button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}

function Field({ label, value, pre }: { label: string; value?: string; pre?: boolean }) {
  return (
    <div>
      <div className="font-display font-bold mb-1">{label}</div>
      {pre ? (
        <pre className="whitespace-pre-wrap bg-ink-50 border-2 border-ink-200 p-2 text-xs max-h-48 overflow-auto">
{value || '—'}
        </pre>
      ) : (
        <div className="font-mono">{value || '—'}</div>
      )}
    </div>
  );
}
