'use client';

import clsx from 'clsx';
import {
  BookOpen,
  ChevronLeft,
  ChevronUp,
  Circle,
  Cpu,
  Database,
  FileText,
  GitGraph,
  HelpCircle,
  LifeBuoy,
  type LucideIcon,
  MessageSquare,
  Plus,
  Settings,
  ShoppingBag,
  Sparkles,
  Star,
  TrendingUp,
  User,
  Users,
} from 'lucide-react';

// TigerGraph Cloud brand mark (orange swirl + logo type).
function BrandMark() {
  return (
    <div className="flex items-center gap-2">
      <div className="flex h-8 w-8 items-center justify-center rounded-md bg-gradient-to-br from-tg-orange to-orange-500">
        <Sparkles size={14} className="text-white" />
      </div>
      <div className="flex flex-col leading-none">
        <span className="text-[9.5px] font-bold uppercase tracking-wider text-tg-mute">
          TigerGraph
        </span>
        <span className="text-[10px] font-bold uppercase tracking-wider text-tg-mute">
          Cloud
        </span>
      </div>
    </div>
  );
}

function NavSection({
  label,
  children,
  collapsible = false,
  badge,
}: {
  label?: string;
  children: React.ReactNode;
  collapsible?: boolean;
  badge?: React.ReactNode;
}) {
  return (
    <div className="mb-1">
      {label && (
        <button
          type="button"
          className="flex w-full items-center justify-between px-2 py-1.5 text-[11px] font-semibold text-tg-ink hover:bg-tg-hover"
        >
          <span className="flex items-center gap-1.5">{label} {badge}</span>
          {collapsible && <ChevronUp size={11} className="text-tg-mute" />}
        </button>
      )}
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

function NavItem({
  icon: Icon,
  label,
  active = false,
  indent = 0,
  badgeIcon,
  rightIcon,
}: {
  icon?: LucideIcon;
  label: string;
  active?: boolean;
  indent?: number;
  badgeIcon?: React.ReactNode;
  rightIcon?: React.ReactNode;
}) {
  return (
    <div
      style={{ paddingLeft: 8 + indent * 12 }}
      className={clsx(
        'flex cursor-pointer items-center gap-2 rounded-md py-1.5 pr-2 text-[12.5px] transition-colors',
        active
          ? 'bg-tg-orange/10 font-semibold text-tg-orange'
          : 'text-tg-ink hover:bg-tg-hover',
      )}
    >
      {Icon && <Icon size={14} className={active ? 'text-tg-orange' : 'text-tg-mute'} />}
      {badgeIcon}
      <span className="flex-1 truncate">{label}</span>
      {rightIcon}
    </div>
  );
}

export default function Sidebar() {
  return (
    <aside className="flex h-screen w-[228px] flex-col border-r border-tg-border bg-tg-panel">
      {/* Brand + collapse */}
      <div className="flex items-center justify-between border-b border-tg-border px-3 py-3">
        <BrandMark />
        <span className="text-[11px] font-semibold text-tg-mute">TG-ORGNIZATION</span>
        <button type="button" className="text-tg-subtle hover:text-tg-ink">
          <ChevronLeft size={14} />
        </button>
      </div>

      {/* Main nav */}
      <div className="flex-1 overflow-y-auto py-2 text-tg-ink">
        <div className="px-2">
          {/* My Workgroups */}
          <NavSection>
            <div className="flex items-center justify-between px-2 py-1.5 text-[12px] font-semibold">
              <span className="flex items-center gap-1.5">
                <span className="grid h-3.5 w-3.5 grid-cols-2 gap-[1.5px]">
                  <span className="bg-tg-mute"></span><span className="bg-tg-mute"></span>
                  <span className="bg-tg-mute"></span><span className="bg-tg-mute"></span>
                </span>
                My Workgroups
                <Plus size={11} className="text-tg-mute" />
              </span>
              <ChevronUp size={11} className="text-tg-mute" />
            </div>

            {/* My Workgroup */}
            <div className="ml-2">
              <div className="flex items-center justify-between px-2 py-1.5 text-[12px] font-medium">
                <span className="flex items-center gap-1.5">
                  <GitGraph size={12} className="text-tg-mute" />
                  My Workgroup
                </span>
                <ChevronUp size={11} className="text-tg-mute" />
              </div>
              <div className="ml-2 space-y-0.5">
                <NavItem icon={Cpu} label="workspace-1" indent={0} />
                <NavItem icon={Cpu} label="workspace-2" indent={0} />
                <NavItem icon={Database} label="fraud-detection" indent={0} />
              </div>
            </div>
          </NavSection>

          <div className="my-2 border-t border-tg-line" />

          {/* Active page */}
          <NavItem icon={TrendingUp} label="Design Schema" active />
          <NavItem icon={Database} label="Load Data" />
          <NavItem icon={FileText} label="GSQL Editor" />
          <NavItem icon={GitGraph} label="Explore Graph" />

          <div className="my-2 border-t border-tg-line" />

          {/* Marketplace */}
          <div className="flex items-center justify-between px-2 py-1.5 text-[12px] font-semibold">
            <span className="flex items-center gap-1.5">
              <ShoppingBag size={12} className="text-tg-mute" />
              Marketplace
            </span>
            <ChevronUp size={11} className="text-tg-mute" />
          </div>
          <NavItem icon={LifeBuoy} label="Solution" indent={0} />
          <NavItem icon={ShoppingBag} label="Add-Ons" indent={0} />

          <div className="my-2 border-t border-tg-line" />

          {/* Admin */}
          <div className="flex items-center justify-between px-2 py-1.5 text-[12px] font-semibold">
            <span className="flex items-center gap-1.5">
              <Users size={12} className="text-tg-mute" />
              Admin
            </span>
            <ChevronUp size={11} className="text-tg-mute" />
          </div>
          <NavItem icon={User} label="User" indent={0} />
          <NavItem icon={FileText} label="Audit Log" indent={0} />
          <NavItem icon={ShoppingBag} label="Billing" indent={0} />
          <NavItem icon={Settings} label="Settings" indent={0} />
        </div>
      </div>

      {/* Bottom links + identity */}
      <div className="border-t border-tg-border px-2 py-2 text-tg-ink">
        <NavItem icon={MessageSquare} label="Community" />
        <NavItem icon={Star} label="Feedback" />
        <NavItem icon={BookOpen} label="Docs" />
        <NavItem icon={FileText} label="Release notes" />
        <NavItem icon={HelpCircle} label="Support" />
        <NavItem icon={LifeBuoy} label="Tutorial" />
      </div>

      <div className="border-t border-tg-border px-3 py-2">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-tg-orange text-[10px] font-medium text-white">
            DS
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[11.5px] text-tg-ink">devanshu.saxena@tigergraph.c…</p>
          </div>
          <Circle size={4} fill="currentColor" className="text-tg-subtle" />
        </div>
      </div>
    </aside>
  );
}
