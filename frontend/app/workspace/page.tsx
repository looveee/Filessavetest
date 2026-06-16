'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { PageHeader, StatusBadge, EmptyState } from '@/components/UI';

export default function WorkspacePage() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const reload = () => api.workspace().then(setData).finally(() => setLoading(false));
  useEffect(() => { reload(); }, []);

  if (loading) return <div className="font-mono text-sm">loading…</div>;
  if (!data) return <EmptyState>无数据</EmptyState>;

  return (
    <>
      <PageHeader title="My Workspace" subtitle="所有跟你相关的事，都在这一页" />

      <Section title="我发起的项目" link="/projects">
        {data.my_projects?.length ? (
          <div className="grid md:grid-cols-3 gap-3">
            {data.my_projects.map((p: any) => (
              <Link key={p.id} href={`/projects/${p.id}`}
                    className="card hover:shadow-hard hover:-translate-y-0.5 transition">
                <div className="flex items-start justify-between">
                  <span className="font-display font-bold">{p.name}</span>
                  <StatusBadge status={p.status}/>
                </div>
                {p.genre && <span className="tag mt-2 inline-block">{p.genre}</span>}
              </Link>
            ))}
          </div>
        ) : <EmptyState>没有自己的项目</EmptyState>}
      </Section>

      <Section title="待我处理任务">
        <TaskGrid items={data.tasks_for_me} emptyText="🎉 没有待处理任务" />
      </Section>

      <Section title="待我审核的视频">
        <TaskGrid items={data.review_queue} emptyText="审核队列为空" />
      </Section>

      <Section title="待我发布的内容">
        {data.publish_queue?.length ? (
          <div className="space-y-2">
            {data.publish_queue.map((s: any) => (
              <div key={s.id} className="card flex items-center justify-between">
                <div>
                  <div className="font-display font-bold">{s.title || `Asset #${s.asset_id}`}</div>
                  <div className="text-xs font-mono text-ink-500">
                    {s.project_name} · 排定 {new Date(s.scheduled_at).toLocaleString()}
                  </div>
                </div>
                <StatusBadge status={s.status}/>
              </div>
            ))}
          </div>
        ) : <EmptyState>发布队列为空</EmptyState>}
      </Section>
    </>
  );
}

function Section({ title, children, link }: { title: string; children: React.ReactNode; link?: string }) {
  return (
    <section className="mb-10">
      <div className="flex items-end justify-between mb-3">
        <h2 className="font-display text-xl font-bold border-l-4 border-accent pl-3">{title}</h2>
        {link && <Link href={link} className="text-xs font-mono underline text-ink-500">查看全部 →</Link>}
      </div>
      {children}
    </section>
  );
}

function TaskGrid({ items, emptyText }: { items: any[]; emptyText: string }) {
  if (!items?.length) return <EmptyState>{emptyText}</EmptyState>;
  return (
    <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-3">
      {items.map(t => (
        <Link key={t.id} href={`/projects/${t.project_id}`}
              className="card hover:shadow-hard hover:-translate-y-0.5 transition">
          <div className="flex justify-between items-start mb-2">
            <span className="font-mono text-xs">#{t.id}</span>
            <StatusBadge status={t.status}/>
          </div>
          <div className="font-display font-bold text-sm">{t.project_name}</div>
          <div className="text-xs text-ink-500 font-mono mt-1">{t.task_type}</div>
          <div className="flex gap-2 mt-3 flex-wrap">
            {t.ai_score != null && <span className="tag bg-lime">AI {t.ai_score}</span>}
            {(t.risk_flags || []).map((f: string) => (
              <span key={f} className="tag bg-orange-200">⚠ {f}</span>
            ))}
          </div>
        </Link>
      ))}
    </div>
  );
}
