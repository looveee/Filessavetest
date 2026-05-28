'use client';
import { useEffect, useState } from 'react';
import { api, getUser } from '@/lib/api';
import { PageHeader, EmptyState } from '@/components/UI';

export default function SettingsPage() {
  const [status, setStatus] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [prompt, setPrompt] = useState('用一句话介绍你自己。');
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<any>(null);
  const user = typeof window !== 'undefined' ? getUser() : null;
  const isAdmin = !!user?.is_admin;

  const load = async () => {
    setLoading(true);
    try {
      setStatus(await api.aiProviders());
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const runTest = async () => {
    setTesting(true);
    setResult(null);
    try {
      setResult(await api.aiTest(prompt));
    } catch (e: any) {
      setResult({ ok: false, error: e.message });
    } finally {
      setTesting(false);
    }
  };

  if (loading) return <div className="font-mono text-sm">loading…</div>;
  if (err) return <EmptyState title="无法加载" desc={err} />;
  if (!status) return <EmptyState>无数据</EmptyState>;

  return (
    <>
      <PageHeader title="Settings" subtitle="AI Provider 配置状态 · 健康检查 · 测试" />

      <div className="grid md:grid-cols-2 gap-6">
        <section className="card">
          <h3 className="font-display font-bold text-lg mb-4">当前 AI Provider</h3>
          <dl className="space-y-2 text-sm font-mono">
            <Row k="Provider" v={status.current_provider} />
            <Row k="Model" v={status.current_model || '(未设置)'} />
            <Row
              k="Health"
              v={status.configured ? '✅ configured' : '⚠ 未配置'}
              accent={status.configured ? 'lime' : 'orange'}
            />
            {!status.configured && status.missing_config?.length > 0 && (
              <Row k="Missing" v={status.missing_config.join(', ')} accent="orange" />
            )}
          </dl>
          <p className="text-xs text-ink-500 mt-4 font-mono">
            API Key 永不下发到前端，这里只显示是否已配置。修改 provider 请编辑后端 .env 后重启。
          </p>
        </section>

        <section className="card">
          <h3 className="font-display font-bold text-lg mb-4">所有 Provider</h3>
          <table className="w-full text-sm">
            <thead className="font-mono text-xs uppercase text-ink-500">
              <tr>
                <th className="text-left py-1">Provider</th>
                <th className="text-left">Model</th>
                <th className="text-left">Configured</th>
              </tr>
            </thead>
            <tbody>
              {status.providers?.map((p: any) => (
                <tr key={p.name} className="border-t border-ink-200">
                  <td className="py-1.5 font-mono">
                    {p.name}
                    {p.name === status.current_provider && (
                      <span className="tag bg-accent text-white ml-2">当前</span>
                    )}
                  </td>
                  <td className="font-mono text-ink-500">{p.model || '—'}</td>
                  <td>{p.configured ? '✅' : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>

      <section className="card mt-6">
        <h3 className="font-display font-bold text-lg mb-4">Test Prompt</h3>
        {!isAdmin ? (
          <p className="text-sm text-ink-500 font-mono">仅管理员可发起测试调用。</p>
        ) : (
          <>
            <textarea
              className="input w-full h-24 font-mono text-sm"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
            <button
              disabled={testing || !prompt.trim()}
              onClick={runTest}
              className="btn btn-primary mt-3"
            >
              {testing ? '调用中…' : '发送测试'}
            </button>

            {result && (
              <div className="mt-4 border-2 border-ink-900 p-3 text-sm">
                <div className="flex flex-wrap gap-3 font-mono text-xs mb-2">
                  <span>provider: <b>{result.provider}</b></span>
                  <span>model: <b>{result.model || '—'}</b></span>
                  <span>latency: <b>{result.latency_ms}ms</b></span>
                  <span>
                    tokens: <b>{result.input_tokens ?? 0}/{result.output_tokens ?? 0}</b>
                  </span>
                  <span className={result.ok ? 'text-emerald-600' : 'text-red-600'}>
                    {result.ok ? 'OK' : 'FAILED'}
                  </span>
                </div>
                {result.error && (
                  <div className="text-red-600 font-mono text-xs mb-2">{result.error}</div>
                )}
                {result.sample_output && (
                  <pre className="whitespace-pre-wrap bg-ink-50 border-2 border-ink-200 p-2 max-h-64 overflow-auto text-xs">
{result.sample_output}
                  </pre>
                )}
              </div>
            )}
          </>
        )}
      </section>
    </>
  );
}

function Row({ k, v, accent }: { k: string; v: string; accent?: 'lime' | 'orange' }) {
  const bg = accent === 'lime' ? 'bg-lime' : accent === 'orange' ? 'bg-orange-200' : '';
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-ink-500">{k}</dt>
      <dd className={`font-bold px-1 ${bg}`}>{v}</dd>
    </div>
  );
}
