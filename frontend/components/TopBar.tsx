'use client';

import { ChevronDown, GitGraph } from 'lucide-react';

export default function TopBar({ pageTitle = 'Design Schema' }: { pageTitle?: string }) {
  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-tg-border bg-tg-panel px-5">
      <h1 className="text-[13.5px] font-semibold text-tg-ink">{pageTitle}</h1>
      <div className="flex items-center gap-2">
        {/* Global env switcher (visual only) */}
        <button
          type="button"
          className="flex items-center gap-1.5 rounded-md border border-tg-border bg-tg-card px-2.5 py-1 text-[11.5px] text-tg-ink hover:bg-tg-hover"
        >
          <GitGraph size={12} className="text-tg-mute" />
          <span>Global</span>
          <ChevronDown size={11} className="text-tg-mute" />
        </button>
        {/* Workspace selector (visual only) */}
        <button
          type="button"
          className="flex items-center gap-1.5 rounded-md border border-tg-border bg-tg-card px-2.5 py-1 text-[11.5px] text-tg-ink hover:bg-tg-hover"
        >
          <span className="inline-block h-2 w-2 rounded-full bg-tg-orange" />
          <span>My Workgroup / workspace-1</span>
          <span className="h-1.5 w-1.5 rounded-full bg-green-500" />
          <ChevronDown size={11} className="text-tg-mute" />
        </button>
      </div>
    </header>
  );
}
