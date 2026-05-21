'use client';
import { useEffect, useState } from 'react';
import { api, getUser } from '@/lib/api';
import { PageHeader, EmptyState } from '@/components/UI';

export default function UsersPage() {
  const [users, setUsers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [me, setMe] = useState<any>(null);
  const [msg, setMsg] = useState('');

  async function load() {
    setLoading(true);
    try {
      const list = await api.listUsers();
      setUsers(list);
      setMe(getUser());
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

  async function toggleActive(id: number, cur: boolean) {
    try {
      await api.setUserActive(id, !cur);
      flash('已更新');
      load();
    } catch (e: any) {
      flash(e.message || '失败');
    }
  }

  async function toggleAdmin(id: number, cur: boolean) {
    if (!confirm(cur ? '取消该用户的管理员身份？' : '将该用户设为管理员？管理员可访问所有项目。'))
      return;
    try {
      await api.setUserAdmin(id, !cur);
      flash('已更新');
      load();
    } catch (e: any) {
      flash(e.message || '失败');
    }
  }

  const isAdmin = me?.is_admin;

  return (
    <div className="space-y-8">
      <PageHeader
        title="用户管理 / Users"
        subtitle={
          isAdmin
            ? '管理员可禁用账号、授予管理员身份。'
            : '只有管理员能修改用户状态。你看到的是只读视图。'
        }
      />

      {msg && <div className="card border-accent bg-accent/10 font-mono text-sm">{msg}</div>}

      {loading ? (
        <div className="card">加载中…</div>
      ) : users.length === 0 ? (
        <EmptyState title="暂无用户" />
      ) : (
        <div className="card p-0 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-ink-100 border-b-2 border-ink-900">
              <tr className="text-left font-mono text-xs uppercase">
                <th className="p-3">ID</th>
                <th className="p-3">用户名</th>
                <th className="p-3">邮箱</th>
                <th className="p-3">姓名</th>
                <th className="p-3">状态</th>
                <th className="p-3">管理员</th>
                <th className="p-3">注册时间</th>
                {isAdmin && <th className="p-3">操作</th>}
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-b border-ink-200 hover:bg-yellow-50">
                  <td className="p-3 font-mono text-xs">#{u.id}</td>
                  <td className="p-3 font-medium">
                    {u.username}
                    {me?.id === u.id && (
                      <span className="ml-2 tag bg-lime/30 border-ink-900 text-ink-900">
                        我
                      </span>
                    )}
                  </td>
                  <td className="p-3">{u.email}</td>
                  <td className="p-3">{u.full_name || '—'}</td>
                  <td className="p-3">
                    <span
                      className={`badge ${
                        u.is_active
                          ? 'bg-lime/30 border border-ink-900'
                          : 'bg-ink-200 border border-ink-900 line-through'
                      }`}
                    >
                      {u.is_active ? 'active' : 'disabled'}
                    </span>
                  </td>
                  <td className="p-3">
                    {u.is_admin ? (
                      <span className="badge bg-accent/30 border border-ink-900">admin</span>
                    ) : (
                      <span className="text-ink-400">—</span>
                    )}
                  </td>
                  <td className="p-3 text-xs font-mono text-ink-500">
                    {new Date(u.created_at).toLocaleDateString('zh-CN')}
                  </td>
                  {isAdmin && (
                    <td className="p-3">
                      <div className="flex gap-2 flex-wrap">
                        <button
                          className="text-xs underline hover:text-accent"
                          onClick={() => toggleActive(u.id, u.is_active)}
                          disabled={me?.id === u.id}
                        >
                          {u.is_active ? '禁用' : '启用'}
                        </button>
                        <button
                          className="text-xs underline hover:text-accent"
                          onClick={() => toggleAdmin(u.id, u.is_admin)}
                          disabled={me?.id === u.id}
                        >
                          {u.is_admin ? '取消管理员' : '设为管理员'}
                        </button>
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
