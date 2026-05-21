'use client';
import React from 'react';

const STATUS_COLOR: Record<string, string> = {
  pending: 'bg-ink-100 text-ink-900',
  running: 'bg-yellow-300 text-ink-900',
  waiting_human: 'bg-orange-300 text-ink-900',
  assigned: 'bg-blue-200 text-ink-900',
  in_review: 'bg-accent text-white',
  approved: 'bg-lime text-ink-900',
  rejected: 'bg-ink-900 text-white',
  revision_required: 'bg-pink-300 text-ink-900',
  failed: 'bg-red-500 text-white',
  completed: 'bg-emerald-300 text-ink-900',
  published: 'bg-lime text-ink-900',
  skipped: 'bg-ink-200 text-ink-900',
  draft: 'bg-ink-100 text-ink-900',
  active: 'bg-lime text-ink-900',
  archived: 'bg-ink-300 text-ink-900',
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`badge border-2 border-ink-900 ${STATUS_COLOR[status] || 'bg-ink-100'}`}>
      {status}
    </span>
  );
}

export function TaskTypeBadge({ type }: { type: string }) {
  return <span className="tag">{type.replace('_', ' ')}</span>;
}

export function PageHeader({
  title,
  subtitle,
  action,
  right,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
  /** Alias for `action`. If both are passed, both render (right wins position). */
  right?: React.ReactNode;
}) {
  const trailing = right ?? action;
  return (
    <div className="flex items-end justify-between gap-4 mb-8 border-b-2 border-ink-900 pb-4 flex-wrap">
      <div>
        <h1 className="font-display text-3xl md:text-4xl font-bold tracking-tight">
          {title}
        </h1>
        {subtitle && <p className="text-sm text-ink-500 mt-1">{subtitle}</p>}
      </div>
      {trailing}
    </div>
  );
}

export function EmptyState({
  children,
  title,
  desc,
}: {
  children?: React.ReactNode;
  title?: string;
  desc?: string;
}) {
  return (
    <div className="card text-center py-12 text-ink-500 font-mono text-sm">
      {title && <div className="font-display text-2xl text-ink-900 mb-2">{title}</div>}
      {desc && <div className="text-sm">{desc}</div>}
      {children}
    </div>
  );
}
