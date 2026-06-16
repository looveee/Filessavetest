'use client';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { api, setToken, setUser } from '@/lib/api';

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [form, setForm] = useState({ email: '', username: '', password: '', full_name: '' });
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(''); setLoading(true);
    try {
      const data: any = mode === 'login'
        ? await api.login({ username: form.username || form.email, password: form.password })
        : await api.register({
            email: form.email,
            username: form.username,
            password: form.password,
            full_name: form.full_name,
          });
      setToken(data.access_token);
      setUser(data.user);
      router.push('/dashboard');
    } catch (e: any) {
      setErr(e.message);
    } finally { setLoading(false); }
  };

  return (
    <div className="min-h-screen grid md:grid-cols-2">
      {/* Left: art */}
      <div className="hidden md:flex relative bg-ink-900 text-white p-12 flex-col justify-between overflow-hidden">
        <div className="absolute inset-0 opacity-30"
             style={{ backgroundImage: 'radial-gradient(#ff5436 1px, transparent 1px)', backgroundSize: '24px 24px' }} />
        <div className="relative z-10">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-accent border-2 border-white shadow-hard-sm flex items-center justify-center">
              <span className="font-display font-bold text-sm">SV</span>
            </div>
            <span className="font-display font-bold tracking-tight text-lg">ShortVideo·OS</span>
          </div>
        </div>
        <div className="relative z-10">
          <h1 className="font-display font-bold text-5xl leading-[1.05] tracking-tight">
            AI<br/>SHORT-VIDEO<br/>
            <span className="text-accent">PIPELINE</span>
          </h1>
          <p className="mt-6 max-w-sm font-mono text-sm text-ink-300">
            从主题 → 大纲 → 分集 → 文案 → 分镜 → 成片 → 排期发布<br/>
            一条流水线，一次拉通。<br/>
            <span className="text-lime">人工在环 · 多人协作 · 项目隔离</span>
          </p>
        </div>
        <div className="relative z-10 font-mono text-xs text-ink-400">
          v0.1 · MVP · self-hosted
        </div>
      </div>

      {/* Right: form */}
      <div className="flex items-center justify-center p-8 bg-white">
        <form onSubmit={submit} className="w-full max-w-sm space-y-4">
          <div className="mb-6">
            <h2 className="font-display text-2xl font-bold">
              {mode === 'login' ? '欢迎回来' : '创建账号'}
            </h2>
            <p className="text-sm text-ink-500 font-mono mt-1">
              {mode === 'login' ? '继续制作下一爆款' : '第一个注册者将自动成为管理员'}
            </p>
          </div>

          {mode === 'register' && (
            <>
              <div>
                <label className="text-xs font-mono uppercase tracking-wider">Email</label>
                <input className="input mt-1" type="email" required
                       value={form.email} onChange={e => setForm({...form, email: e.target.value})}/>
              </div>
              <div>
                <label className="text-xs font-mono uppercase tracking-wider">姓名 (可选)</label>
                <input className="input mt-1" value={form.full_name}
                       onChange={e => setForm({...form, full_name: e.target.value})}/>
              </div>
            </>
          )}

          <div>
            <label className="text-xs font-mono uppercase tracking-wider">
              {mode === 'login' ? '用户名 / Email' : '用户名'}
            </label>
            <input className="input mt-1" required value={form.username}
                   onChange={e => setForm({...form, username: e.target.value})}/>
          </div>

          <div>
            <label className="text-xs font-mono uppercase tracking-wider">密码</label>
            <input className="input mt-1" type="password" required minLength={6}
                   value={form.password}
                   onChange={e => setForm({...form, password: e.target.value})}/>
          </div>

          {err && <div className="text-sm text-red-600 font-mono border-2 border-red-600 px-3 py-2">{err}</div>}

          <button disabled={loading} className="btn btn-primary w-full">
            {loading ? '...' : mode === 'login' ? '登录' : '注册'}
          </button>

          <button type="button"
                  onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
                  className="block w-full text-center text-sm font-mono text-ink-500 hover:text-ink-900 underline">
            {mode === 'login' ? '没有账号？ 去注册' : '已有账号？ 去登录'}
          </button>
        </form>
      </div>
    </div>
  );
}
