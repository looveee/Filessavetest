'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { PageHeader, StatusBadge, EmptyState } from '@/components/UI';

export default function ProjectDetailPage({ params }: { params: { id: string } }) {
  const id = Number(params.id);
  const [proj, setProj] = useState<any>(null);
  const [tab, setTab] = useState<'pipeline'|'episodes'|'tasks'|'members'>('pipeline');
  const [episodes, setEpisodes] = useState<any[]>([]);
  const [tasks, setTasks] = useState<any[]>([]);
  const [members, setMembers] = useState<any[]>([]);
  const [aiRuns, setAiRuns] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [theme, setTheme] = useState('');
  const [activeEp, setActiveEp] = useState<any>(null);
  const [scripts, setScripts] = useState<any[]>([]);
  const [storyboards, setStoryboards] = useState<any[]>([]);

  const reload = async () => {
    const p = await api.getProject(id);
    setProj(p);
    setTheme(p.name);
    const [eps, ts, ms, runs] = await Promise.all([
      api.listEpisodes(id), api.listTasks({ project_id: id }), api.listMembers(id),
      api.listAiRuns({ project_id: id }).catch(() => []),
    ]);
    setEpisodes(eps); setTasks(ts); setMembers(ms); setAiRuns(runs || []);
  };

  // latest AI run per task_id (runs come back newest-first)
  const runForTask = (taskId: number) => aiRuns.find((r: any) => r.task_id === taskId);

  useEffect(() => { reload(); /* eslint-disable-next-line */ }, [id]);

  const flash = (s: string) => { setMsg(s); setTimeout(() => setMsg(''), 2500); };

  const startOutline = async () => {
    setBusy(true);
    try {
      await api.createTask({ project_id: id, task_type: 'outline_generation', input_data: { theme } });
      flash('大纲生成任务已派发'); await reload();
    } catch (e: any) { flash('失败：' + e.message); }
    finally { setBusy(false); }
  };

  const splitEpisodes = async () => {
    setBusy(true);
    try {
      const lastOutline = tasks.find(t => t.task_type === 'outline_generation' && t.status === 'completed');
      const outline = lastOutline?.output_data || {};
      await api.createTask({
        project_id: id, task_type: 'episode_split',
        input_data: { outline, total: proj.target_episodes || 10 },
      });
      flash('分集拆分任务已派发'); await reload();
    } catch (e: any) { flash('失败：' + e.message); }
    finally { setBusy(false); }
  };

  const genScriptForEp = async (epId: number) => {
    setBusy(true);
    try {
      await api.createTask({ project_id: id, task_type: 'script_generation', episode_id: epId });
      flash('文案生成已派发'); await reload();
    } catch (e: any) { flash('失败：' + e.message); }
    finally { setBusy(false); }
  };

  const openEp = async (ep: any) => {
    setActiveEp(ep);
    const [ss, sb] = await Promise.all([api.listScripts(ep.id), api.listStoryboards(ep.id)]);
    setScripts(ss); setStoryboards(sb);
  };

  const addMember = async (uid: number, role: string) => {
    try { await api.addMember(id, { user_id: uid, role }); flash('成员已添加'); await reload(); }
    catch (e: any) { flash('失败：' + e.message); }
  };

  const upload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files?.[0]) return;
    setBusy(true);
    try { await api.uploadSource(id, e.target.files[0]); flash('小说上传成功'); }
    catch (err: any) { flash('失败：' + err.message); }
    finally { setBusy(false); e.target.value = ''; }
  };

  if (!proj) return <div className="font-mono text-sm">loading…</div>;

  return (
    <>
      <PageHeader
        title={proj.name}
        subtitle={`${proj.genre || ''} · ${proj.style || ''} · 目标 ${proj.target_episodes} 集`}
        action={<StatusBadge status={proj.status} />}
      />

      {msg && <div className="mb-4 border-2 border-ink-900 bg-lime px-3 py-2 text-sm font-mono">{msg}</div>}

      {/* Tabs */}
      <div className="flex gap-1 mb-6 border-b-2 border-ink-900">
        {(['pipeline','episodes','tasks','members'] as const).map(t => (
          <button key={t} onClick={() => setTab(t)}
                  className={`px-4 py-2 -mb-0.5 border-2 text-sm font-medium ${
                    tab === t ? 'border-ink-900 border-b-white bg-white' : 'border-transparent text-ink-500'}`}>
            {t === 'pipeline' ? '流水线' : t === 'episodes' ? '分集' : t === 'tasks' ? '任务' : '成员'}
          </button>
        ))}
      </div>

      {tab === 'pipeline' && (
        <div className="space-y-6">
          <div className="card">
            <h3 className="font-display font-bold text-lg mb-4">① 输入主题 / 上传小说</h3>
            <div className="flex flex-col md:flex-row gap-3">
              <input className="input flex-1" value={theme} onChange={e => setTheme(e.target.value)}
                     placeholder="例如：复仇女王回归之路"/>
              <label className="btn btn-ghost cursor-pointer">
                上传 .txt
                <input type="file" accept=".txt" hidden onChange={upload}/>
              </label>
              <button disabled={busy} onClick={startOutline} className="btn btn-primary">生成大纲</button>
            </div>
          </div>

          <div className="card">
            <h3 className="font-display font-bold text-lg mb-4">② 拆分分集</h3>
            <button disabled={busy} onClick={splitEpisodes} className="btn btn-dark">
              基于最新大纲拆分 {proj.target_episodes || 10} 集
            </button>
          </div>

          <div className="card">
            <h3 className="font-display font-bold text-lg mb-4">③ 给每一集生成文案 → 分镜 → 视频</h3>
            <p className="text-sm text-ink-500 font-mono mb-3">在「分集」页签中对每一集触发生成，并在「Review」中审核。</p>
            <p className="text-xs font-mono text-ink-500">
              审核通过后，下游任务会自动继续：<br/>
              script ✅ → storyboard auto-queue<br/>
              storyboard ✅ → video auto-queue<br/>
              video（mock 占位）→ 进入 Review → 通过后入 Assets
            </p>
          </div>
        </div>
      )}

      {tab === 'episodes' && (
        <div className="grid md:grid-cols-3 gap-4">
          <div className="md:col-span-1 space-y-2 max-h-[70vh] overflow-auto pr-1">
            {episodes.length === 0 && <EmptyState>没有分集，先在流水线页签拆分。</EmptyState>}
            {episodes.map(ep => (
              <button key={ep.id} onClick={() => openEp(ep)}
                      className={`block w-full text-left card hover:shadow-hard transition ${
                        activeEp?.id === ep.id ? 'bg-lime' : ''}`}>
                <div className="flex justify-between items-start">
                  <div>
                    <div className="font-display font-bold">第{ep.episode_number}集</div>
                    <div className="text-xs text-ink-500 line-clamp-1">{ep.title}</div>
                  </div>
                  <StatusBadge status={ep.status} />
                </div>
                {ep.ai_score && <div className="text-xs font-mono mt-1">AI {ep.ai_score}</div>}
              </button>
            ))}
          </div>

          <div className="md:col-span-2 card">
            {!activeEp ? <EmptyState>选择一集查看详情</EmptyState> : (
              <>
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h3 className="font-display font-bold text-xl">第{activeEp.episode_number}集</h3>
                    <p className="text-sm text-ink-500">{activeEp.title}</p>
                  </div>
                  <button onClick={() => genScriptForEp(activeEp.id)}
                          disabled={busy} className="btn btn-primary text-sm">
                    生成 / 重新生成文案
                  </button>
                </div>

                <h4 className="font-display font-bold mt-4 mb-2">最新文案</h4>
                {scripts[0] ? (
                  <pre className="whitespace-pre-wrap text-sm bg-ink-50 border-2 border-ink-200 p-3 max-h-64 overflow-auto">
{scripts[0].content}
                  </pre>
                ) : <p className="text-sm text-ink-500 font-mono">暂无</p>}

                <h4 className="font-display font-bold mt-6 mb-2">分镜 ({storyboards.length})</h4>
                {storyboards.length === 0 ? (
                  <p className="text-sm text-ink-500 font-mono">通过文案审核后会自动生成。</p>
                ) : (
                  <div className="space-y-2 max-h-64 overflow-auto">
                    {storyboards.map(sb => (
                      <div key={sb.id} className="border-2 border-ink-300 p-2 text-xs">
                        <div className="flex justify-between font-mono">
                          <span>#{sb.shot_number} · {sb.duration_sec}s</span>
                          <span className="text-ink-500">{sb.sfx}</span>
                        </div>
                        <div className="mt-1">{sb.visual}</div>
                        <div className="text-ink-500 mt-0.5">VO: {sb.voiceover}</div>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {tab === 'tasks' && (
        <div className="space-y-2">
          {tasks.length === 0 && <EmptyState>还没有任务</EmptyState>}
          {tasks.map(t => (
            <div key={t.id} className="card flex items-center justify-between gap-4">
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <span className="font-mono text-sm">#{t.id}</span>
                  <span className="tag">{t.task_type}</span>
                  <StatusBadge status={t.status} />
                  {t.ai_score != null && <span className="tag bg-lime">AI {t.ai_score}</span>}
                </div>
                {t.error && <div className="text-xs text-red-600 font-mono">{t.error}</div>}
                {(t.risk_flags?.length > 0) && (
                  <div className="text-xs text-orange-600 font-mono">⚠ {t.risk_flags.join(', ')}</div>
                )}
                {(() => {
                  const run = runForTask(t.id);
                  if (!run) return null;
                  return (
                    <div className="text-xs text-ink-500 font-mono mt-1 flex flex-wrap gap-x-3">
                      <span>{run.provider}/{run.model || '—'}</span>
                      <span>tok {run.input_tokens}/{run.output_tokens}</span>
                      <span>{run.latency_ms}ms</span>
                      {run.prompt_template_key && (
                        <span>{run.prompt_template_key}.v{run.prompt_template_version}</span>
                      )}
                      <span className={run.status === 'failed' ? 'text-red-600' : 'text-emerald-600'}>
                        {run.status}
                      </span>
                    </div>
                  );
                })()}
              </div>
              {t.status === 'failed' && (
                <button onClick={async () => { await api.retryTask(t.id); reload(); }}
                        className="btn btn-ghost text-sm">重试</button>
              )}
            </div>
          ))}
        </div>
      )}

      {tab === 'members' && (
        <div className="space-y-4">
          <div className="card">
            <h3 className="font-display font-bold mb-3">添加成员</h3>
            <AddMemberRow existingIds={members.map(m => m.user_id)} onAdd={addMember}/>
          </div>
          <div className="card">
            <h3 className="font-display font-bold mb-3">当前成员</h3>
            <table className="w-full text-sm">
              <thead className="font-mono text-xs uppercase text-ink-500">
                <tr><th className="text-left py-2">User</th><th className="text-left">Role</th><th></th></tr>
              </thead>
              <tbody>
                {members.map(m => {
                  return (
                    <tr key={m.id} className="border-t border-ink-200">
                      <td className="py-2">user#{m.user_id}</td>
                      <td><span className="tag">{m.role}</span></td>
                      <td className="text-right">
                        <button onClick={async () => { await api.removeMember(id, m.user_id); reload(); }}
                                className="text-xs underline text-ink-500 hover:text-red-600">移除</button>
                      </td>
                    </tr>
                  );
                })}
                {members.length === 0 && <tr><td className="py-3 text-ink-500 font-mono text-sm" colSpan={3}>仅你自己（owner）</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}

function AddMemberRow({ existingIds, onAdd }: { existingIds: number[]; onAdd: (uid: number, role: string) => void }) {
  const [username, setUsername] = useState('');
  const [role, setRole] = useState('editor');
  const [found, setFound] = useState<any | null>(null);
  const [searching, setSearching] = useState(false);
  const [msg, setMsg] = useState('');

  async function search() {
    setMsg('');
    setFound(null);
    if (!username.trim()) return;
    setSearching(true);
    try {
      const r: any[] = await api.lookupUser(username.trim());
      if (r.length === 0) {
        setMsg('未找到该用户名');
      } else {
        const u = r[0];
        if (existingIds.includes(u.id)) {
          setMsg('该用户已在项目中');
          setFound(u);
        } else {
          setFound(u);
        }
      }
    } catch (e: any) {
      setMsg(e.message || '查询失败');
    } finally {
      setSearching(false);
    }
  }

  function add() {
    if (!found) return;
    onAdd(found.id, role);
    setUsername('');
    setFound(null);
    setMsg('');
  }

  const isExisting = found && existingIds.includes(found.id);

  return (
    <div className="space-y-2">
      <div className="flex flex-col md:flex-row gap-2">
        <input
          className="input"
          placeholder="输入用户名（精确匹配）"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') search(); }}
        />
        <button onClick={search} disabled={searching || !username.trim()} className="btn btn-dark md:w-32">
          {searching ? '...' : '查找'}
        </button>
        <select className="input md:w-48" value={role} onChange={(e) => setRole(e.target.value)}>
          {['manager','editor','storyboarder','reviewer','publisher','viewer'].map((r) => (
            <option key={r}>{r}</option>
          ))}
        </select>
        <button disabled={!found || isExisting} onClick={add} className="btn btn-primary md:w-32">
          添加
        </button>
      </div>
      {found && !isExisting && (
        <div className="text-xs font-mono text-ink-700 border-2 border-lime bg-lime/20 p-2">
          ✓ 找到：@{found.username}{found.full_name ? ` · ${found.full_name}` : ''} (id={found.id})
        </div>
      )}
      {msg && (
        <div className="text-xs font-mono text-accent">{msg}</div>
      )}
    </div>
  );
}
