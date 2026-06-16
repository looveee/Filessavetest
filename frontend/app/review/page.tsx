'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { PageHeader, StatusBadge, TaskTypeBadge, EmptyState } from '@/components/UI';

const ACTIONS = [
  { key: 'approve', label: '通过', cls: 'btn-lime' },
  { key: 'rewrite', label: '重写', cls: 'btn-dark' },
  { key: 'enhance_conflict', label: '加强冲突', cls: 'btn-dark' },
  { key: 'enhance_cliffhanger', label: '加强悬念', cls: 'btn-dark' },
  { key: 'enhance_hook', label: '加强钩子', cls: 'btn-dark' },
  { key: 'redo', label: '重做', cls: 'btn-primary' },
  { key: 'reject', label: '驳回', cls: 'btn-ghost' },
];

export default function ReviewPage() {
  const [tasks, setTasks] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [scripts, setScripts] = useState<Record<number, any>>({});
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [msg, setMsg] = useState('');
  const [filter, setFilter] = useState<string>('all');

  async function load() {
    setLoading(true);
    try {
      // Load tasks needing human attention
      const waiting = await api.listTasks({ status: 'waiting_human' });
      const inReview = await api.listTasks({ status: 'in_review' });
      const merged = [...waiting, ...inReview];
      setTasks(merged);

      // For script tasks, prefetch latest script
      const scriptMap: Record<number, any> = {};
      for (const t of merged) {
        if (t.task_type === 'script_generation' && t.episode_id) {
          try {
            const ss = await api.listScripts(t.episode_id);
            if (ss && ss.length) {
              scriptMap[t.id] = ss[ss.length - 1];
            }
          } catch (e) {}
        }
      }
      setScripts(scriptMap);
    } catch (e: any) {
      setMsg(e.message || '加载失败');
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

  async function act(taskId: number, action: string) {
    setBusy(taskId);
    try {
      await api.reviewTask(taskId, action, notes[taskId] || '');
      flash(`已执行：${action}`);
      await load();
    } catch (e: any) {
      flash(e.message || '操作失败');
    } finally {
      setBusy(null);
    }
  }

  const visible = tasks.filter((t) => filter === 'all' || t.task_type === filter);

  return (
    <div className="space-y-8">
      <PageHeader
        title="审核台 / Review Queue"
        subtitle="人工在环：审阅 AI 生成结果，决定通过、改写或重做。"
        right={
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="input w-auto"
          >
            <option value="all">全部类型</option>
            <option value="script_generation">文案</option>
            <option value="storyboard_generation">分镜</option>
            <option value="video_generation">视频</option>
          </select>
        }
      />

      {msg && (
        <div className="card border-accent bg-accent/10 text-ink-900 font-mono text-sm">
          {msg}
        </div>
      )}

      {loading ? (
        <div className="card">加载中…</div>
      ) : visible.length === 0 ? (
        <EmptyState
          title="暂无待审核任务"
          desc="当 AI 完成文案/分镜/视频生成，待你确认的任务会出现在这里。"
        />
      ) : (
        <div className="space-y-6">
          {visible.map((t) => {
            const sc = scripts[t.id];
            return (
              <div key={t.id} className="card space-y-4">
                <div className="flex items-start justify-between gap-4 flex-wrap">
                  <div>
                    <div className="flex items-center gap-2 flex-wrap">
                      <TaskTypeBadge type={t.task_type} />
                      <StatusBadge status={t.status} />
                      {typeof t.ai_score === 'number' && (
                        <span className="badge bg-lime/30 border border-ink-900">
                          AI: {t.ai_score.toFixed(0)}
                        </span>
                      )}
                    </div>
                    <h3 className="font-display text-xl mt-2">
                      {t.project_name || `项目 #${t.project_id}`}
                      {t.episode_number && (
                        <span className="text-ink-500 ml-2">
                          · 第 {t.episode_number} 集
                        </span>
                      )}
                    </h3>
                    {t.risk_flags && t.risk_flags.length > 0 && (
                      <div className="flex gap-2 mt-2 flex-wrap">
                        {t.risk_flags.map((r: string, i: number) => (
                          <span
                            key={i}
                            className="tag bg-accent/20 border-accent text-accent"
                          >
                            ⚠ {r}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="text-xs text-ink-500 font-mono">
                    #{t.id} · {new Date(t.updated_at).toLocaleString('zh-CN')}
                  </div>
                </div>

                {sc && (
                  <div className="border-2 border-ink-900 bg-ink-50 p-4">
                    <div className="text-xs font-mono text-ink-500 mb-2">
                      文案 v{sc.version} · {sc.title}
                    </div>
                    <pre className="whitespace-pre-wrap text-sm font-sans leading-relaxed max-h-72 overflow-auto">
                      {sc.content}
                    </pre>
                    {sc.tags && sc.tags.length > 0 && (
                      <div className="flex gap-2 mt-2 flex-wrap">
                        {sc.tags.map((tag: string, i: number) => (
                          <span key={i} className="tag">
                            #{tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {t.task_type === 'video_generation' && t.output_data?.video_path && (
                  <div className="border-2 border-ink-900 bg-ink-50 p-4">
                    <div className="text-xs font-mono text-ink-500 mb-2">
                      视频产物（mock）
                    </div>
                    <code className="text-sm break-all">
                      {t.output_data.video_path}
                    </code>
                  </div>
                )}

                <textarea
                  className="input min-h-[60px]"
                  placeholder="审核备注（可选）：写明你希望的修改方向…"
                  value={notes[t.id] || ''}
                  onChange={(e) =>
                    setNotes((n) => ({ ...n, [t.id]: e.target.value }))
                  }
                />

                <div className="flex flex-wrap gap-2">
                  {ACTIONS.map((a) => (
                    <button
                      key={a.key}
                      disabled={busy === t.id}
                      className={`btn ${a.cls}`}
                      onClick={() => act(t.id, a.key)}
                    >
                      {busy === t.id ? '...' : a.label}
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
