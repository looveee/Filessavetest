import './globals.css';
import type { Metadata } from 'next';
import Nav from '@/components/Nav';

export const metadata: Metadata = {
  title: 'ShortVideo OS — AI Production Pipeline',
  description: '半自动 AI 短视频生产平台',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen">
        <Nav />
        <main className="max-w-7xl mx-auto px-6 py-8">{children}</main>
        <footer className="border-t-2 border-ink-900 mt-20 py-6 text-center text-xs font-mono text-ink-500">
          ShortVideo OS · MVP · 半自动模式 · 数据本地化
        </footer>
      </body>
    </html>
  );
}
