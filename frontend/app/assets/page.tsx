'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api, getToken } from '@/lib/api';
import { PageHeader, StatusBadge, EmptyState } from '@/components/UI';

async function downloadAssetWithAuth(asset: any) {
  // Fetch with Bearer token, then trigger a save dialog from the blob.
  // We can't use a plain <a href> because the endpoint needs the JWT.
  const tok = getToken();
  const resp = await fetch(api.assetDownloadPath(asset.id), {
    headers: tok ? { Authorization: `Bearer ${tok}` } : {},
  });
  if (!resp.ok) {
    alert(`下载失败: ${resp.status}`);
    return;
  }
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `asset_${asset.id}.mp4`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export default function AssetsPage() {
  const [assets, setAssets] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');

  async function load() {
    setLoading(true);
    try {
      const list = await api.listAssets();
      setAssets(list);
    } catch (e: any) {
      setErr(e.message || '加载失败');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="space-y-8">
      <PageHeader
        title="成片库 / Assets"
        subtitle="所有已生成的视频资产，按项目归档。可直接进入排期发布。"
      />

      {err && <div className="card border-accent text-accent">{err}</div>}

      {loading ? (
        <div className="card">加载中…</div>
      ) : assets.length === 0 ? (
        <EmptyState
          title="还没有成片"
          desc="去项目里跑完文案 → 分镜 → 视频生成的流水线，成片会出现在这里。"
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {assets.map((a) => (
            <div key={a.id} className="card flex flex-col gap-3">
              <div className="aspect-video border-2 border-ink-900 bg-ink-100 flex items-center justify-center font-display text-4xl text-ink-400">
                {a.cover_path ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={a.cover_path}
                    alt=""
                    className="w-full h-full object-cover"
                  />
                ) : (
                  '▶'
                )}
              </div>
              <div className="flex items-center justify-between">
                <h3 className="font-display text-lg truncate flex-1">
                  {a.title || `Asset #${a.id}`}
                </h3>
                <StatusBadge status={a.status} />
              </div>
              <div className="text-xs font-mono text-ink-500 space-y-1">
                <div>Asset #{a.id} · 项目 #{a.project_id}</div>
                {a.episode_id && <div>分集 #{a.episode_id}</div>}
                {a.duration_sec && <div>时长：{a.duration_sec}s</div>}
                <div>类型：{a.asset_type}</div>
              </div>
              <div className="flex gap-2 pt-2 border-t-2 border-ink-900">
                <button
                  className="btn btn-ghost text-xs flex-1"
                  onClick={() => downloadAssetWithAuth(a)}
                >
                  下载
                </button>
                <Link href={`/schedule?asset_id=${a.id}`} className="btn btn-dark flex-1 text-center">
                  排期发布
                </Link>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
