'use client';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { clearToken, getUser } from '@/lib/api';

const NAV = [
  { href: '/dashboard',  label: 'Dashboard' },
  { href: '/workspace',  label: 'Workspace' },
  { href: '/projects',   label: 'Projects' },
  { href: '/review',     label: 'Review' },
  { href: '/assets',     label: 'Assets' },
  { href: '/accounts',   label: 'Accounts' },
  { href: '/schedule',   label: 'Schedule' },
  { href: '/users',      label: 'Users' },
  { href: '/audit',      label: 'Audit' },
  { href: '/prompts',    label: 'Prompts' },
  { href: '/settings',   label: 'Settings' },
];

export default function Nav() {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<any>(null);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    setUser(getUser());
  }, [pathname]);

  if (pathname === '/login' || pathname === '/') return null;
  if (!mounted) return null;

  return (
    <header className="border-b-2 border-ink-900 bg-white sticky top-0 z-30">
      <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        <Link href="/dashboard" className="flex items-center gap-3">
          <div className="w-8 h-8 bg-accent border-2 border-ink-900 shadow-hard-sm flex items-center justify-center">
            <span className="font-display font-bold text-white text-sm">SV</span>
          </div>
          <span className="font-display font-bold tracking-tight">ShortVideo·OS</span>
        </Link>

        <nav className="hidden md:flex items-center gap-1">
          {NAV.map(item => {
            const active = pathname?.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`px-3 py-1.5 text-sm font-medium border-2 transition ${
                  active
                    ? 'border-ink-900 bg-ink-900 text-white'
                    : 'border-transparent hover:border-ink-900 hover:bg-lime'
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="flex items-center gap-3">
          {user && (
            <span className="hidden sm:block text-xs font-mono text-ink-500">
              @{user.username}{user.is_admin ? ' · admin' : ''}
            </span>
          )}
          <button
            onClick={() => { clearToken(); router.push('/login'); }}
            className="btn btn-ghost text-sm py-1 px-3"
          >
            登出
          </button>
        </div>
      </div>
    </header>
  );
}
