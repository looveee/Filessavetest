'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { PageHeader, StatusBadge, EmptyState } from '@/components/UI';

export default function DashboardPage() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.workspace().then(setData).finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="font-mono text-sm">loading…</div>;
  if (!data) return <EmptyState>无数据</EmptyState>;

  const s = data.summary || {};

  return (
    <>
      <PageHeader title="Dashboard" subtitle="生产线全景 · 一眼看清所有进度" />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-10">
        <Stat label="我的项目" value={s.owned_projects} accent />
        <Stat label="待我处理任务" value={s.tasks_pending} />
        <Stat label="待我审核" value={s.review_pending} />
        <Stat label="待我发布" value={s.publish_pending} />
      </div>

      <div className="grid md:grid-cols-2 gap-6">
        <section className="card">
          <h3 className="font-display text-lg font-bold mb-4 flex items-center justify-between">
            <span>我发起的项目</span>
            <Link href="/projects" className="text-xs font-mono underline text-ink-500">全部 →</Link>
          </h3>
          {data.my_projects?.length ? (
            <ul className="divide-y divide-ink-200">
              {data.my_projects.map((p: any) => (
                <li key={p.id} className="py-2 flex items-center justify-between">
                  <Link href={`/projects/${p.id}`} className="hover:underline font-medium">
                    {p.name}
                  </Link>
                  <div className="flex items-center gap-2">
                    {p.genre && <span className="tag">{p.genre}</span>}
                    <StatusBadge status={p.status} />
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-ink-500 font-mono">还没有项目，去创建一个吧。</p>
          )}
        </section>

        <section className="card">
          <h3 className="font-display text-lg font-bold mb-4">待我处理的任务</h3>
          {data.tasks_for_me?.length ? (
            <ul className="space-y-2">
              {data.tasks_for_me.slice(0, 8).map((t: any) => (
                <li key={t.id} className="border-2 border-ink-900 p-3 hover:bg-lime/40 transition">
                  <div className="flex items-center justify-between text-sm">
                    <span className="font-mono">#{t.id} · {t.task_type}</span>
                    <StatusBadge status={t.status} />
                  </div>
                  <div className="text-xs text-ink-500 mt-1 font-mono">
                    {t.project_name} {t.ai_score != null && ` · AI ${t.ai_score}`}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-ink-500 font-mono">没有待处理任务 🎉</p>
          )}
        </section>
      </div>
    </>
  );
}

function Stat({ label, value, accent = false }: { label: string; value: number; accent?: boolean }) {
  return (
    <div className={`card ${accent ? 'bg-accent text-white' : ''}`}>
      <div className="text-xs font-mono uppercase tracking-wider opacity-70">{label}</div>
      <div className="font-display text-4xl font-bold mt-1">{value ?? 0}</div>
    </div>
  );
}
