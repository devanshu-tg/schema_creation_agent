'use client';

import { Sparkles } from 'lucide-react';

const TGLogo = () => (
  <div className="flex items-center gap-2">
    <div className="flex h-7 w-7 items-center justify-center rounded-md bg-gradient-to-br from-tg-orange to-orange-500">
      <span className="text-[11px] font-bold text-white">TG</span>
    </div>
    <span className="text-[13px] font-semibold text-tg-ink">TG-ORGANIZATION</span>
  </div>
);

export default function Sidebar() {
  return (
    <aside className="flex h-screen w-[256px] flex-col border-r border-tg-border bg-tg-panel">
      {/* Top: brand */}
      <div className="border-b border-tg-border px-3 py-3">
        <TGLogo />
      </div>

      {/* Workspace + active page */}
      <div className="flex-1 overflow-y-auto px-3 py-3">
        <div className="mb-4">
          <div className="mb-1 px-2 text-[10px] font-semibold uppercase tracking-wide text-tg-subtle">
            Workspace
          </div>
          <div className="flex items-center gap-2 rounded-md px-2 py-1.5 text-[13px] text-tg-mute">
            <span className="h-1.5 w-1.5 rounded-full bg-tg-purple-500" />
            <span className="font-medium text-tg-ink">fraud-detection</span>
          </div>
        </div>

        <div>
          <div className="mb-1 px-2 text-[10px] font-semibold uppercase tracking-wide text-tg-subtle">
            Page
          </div>
          <div className="flex items-center gap-2 rounded-md bg-tg-purple-100 px-2 py-1.5 text-[13px] font-medium text-tg-purple-700">
            <Sparkles size={14} className="text-tg-purple-500" />
            <span>Design Schema</span>
          </div>
        </div>
      </div>

      {/* Bottom: user identity (informational, not clickable) */}
      <div className="border-t border-tg-border px-3 py-3">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-tg-orange text-[10px] font-medium text-white">
            DS
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[12px] text-tg-ink">devanshu.saxena@tigergraph.com</p>
          </div>
        </div>
      </div>
    </aside>
  );
}
