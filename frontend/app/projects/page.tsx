'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { PageHeader, StatusBadge, EmptyState } from '@/components/UI';

export default function ProjectsPage() {
  const [list, setList] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: '', description: '', genre: '都市', style: '快节奏', target_episodes: 10 });
  const [loading, setLoading] = useState(true);

  const reload = async () => {
    setLoading(true);
    try { setList(await api.listProjects()); } finally { setLoading(false); }
  };

  useEffect(() => { reload(); }, []);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    await api.createProject({ ...form, target_episodes: Number(form.target_episodes) });
    setOpen(false);
    setForm({ name: '', description: '', genre: '都市', style: '快节奏', target_episodes: 10 });
    reload();
  };

  return (
    <>
      <PageHeader
        title="Projects"
        subtitle="每个项目独立运行 · 仅你和被授权成员可见"
        action={<button onClick={() => setOpen(true)} className="btn btn-primary">+ 新建项目</button>}
      />

      {loading ? <div className="font-mono text-sm">loading…</div>
        : list.length === 0 ? <EmptyState>还没有项目。点击右上角创建你的第一个项目。</EmptyState>
        : (
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
            {list.map(p => (
              <Link key={p.id} href={`/projects/${p.id}`}
                    className="card hover:shadow-hard hover:-translate-y-1 transition cursor-pointer">
                <div className="flex items-start justify-between mb-3">
                  <h3 className="font-display font-bold text-lg leading-tight">{p.name}</h3>
                  <StatusBadge status={p.status} />
                </div>
                <p className="text-sm text-ink-500 mb-4 line-clamp-2 min-h-[2.5rem]">
                  {p.description || '— 暂无描述 —'}
                </p>
                <div className="flex items-center gap-2 flex-wrap">
                  {p.genre && <span className="tag">{p.genre}</span>}
                  {p.style && <span className="tag">{p.style}</span>}
                  <span className="tag bg-ink-900 text-white">目标 {p.target_episodes} 集</span>
                </div>
              </Link>
            ))}
          </div>
        )}

      {open && (
        <div className="fixed inset-0 bg-ink-900/50 flex items-center justify-center z-50 p-4"
             onClick={() => setOpen(false)}>
          <form onSubmit={create} onClick={e => e.stopPropagation()}
                className="bg-white border-2 border-ink-900 shadow-hard p-6 w-full max-w-md space-y-3">
            <h3 className="font-display text-xl font-bold">新建项目</h3>
            <div>
              <label className="text-xs font-mono uppercase">名称</label>
              <input className="input mt-1" required value={form.name}
                     onChange={e => setForm({...form, name: e.target.value})}/>
            </div>
            <div>
              <label className="text-xs font-mono uppercase">描述</label>
              <textarea className="input mt-1" rows={2} value={form.description}
                        onChange={e => setForm({...form, description: e.target.value})}/>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="text-xs font-mono uppercase">题材</label>
                <input className="input mt-1" value={form.genre}
                       onChange={e => setForm({...form, genre: e.target.value})}/>
              </div>
              <div>
                <label className="text-xs font-mono uppercase">风格</label>
                <input className="input mt-1" value={form.style}
                       onChange={e => setForm({...form, style: e.target.value})}/>
              </div>
              <div>
                <label className="text-xs font-mono uppercase">集数</label>
                <input className="input mt-1" type="number" min={1} value={form.target_episodes}
                       onChange={e => setForm({...form, target_episodes: Number(e.target.value) as any})}/>
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={() => setOpen(false)} className="btn btn-ghost">取消</button>
              <button className="btn btn-dark">创建</button>
            </div>
          </form>
        </div>
      )}
    </>
  );
}
