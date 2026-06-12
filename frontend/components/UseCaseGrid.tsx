'use client';

import clsx from 'clsx';
import {
  BookOpen,
  Database,
  type LucideIcon,
  Network,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Truck,
  Users,
} from 'lucide-react';
import type { UseCase } from '@/lib/types';

interface UseCaseDef {
  id: UseCase;
  label: string;
  description: string;
  icon: LucideIcon;
  enabled: boolean;
}

const USE_CASES: UseCaseDef[] = [
  {
    id: 'FRAUD',
    label: 'Fraud detection',
    description: 'Detect rings, shared infrastructure, mule networks',
    icon: ShieldAlert,
    enabled: true,
  },
  {
    id: 'CUSTOMER_360',
    label: 'Customer 360',
    description: 'Unified view of orders, support, sessions',
    icon: Users,
    enabled: true,
  },
  {
    id: 'ENTITY_RESOLUTION',
    label: 'Entity resolution',
    description: 'Resolve duplicates across CRM, billing, support',
    icon: Sparkles,
    enabled: true,
  },
  {
    id: 'RECOMMENDATION',
    label: 'Recommendation',
    description: 'Users, items, interactions for collaborative filtering',
    icon: Database,
    enabled: true,
  },
  {
    id: 'SUPPLY_CHAIN',
    label: 'Supply chain',
    description: 'Suppliers, shipments, bottleneck analysis',
    icon: Truck,
    enabled: true,
  },
  {
    id: 'CYBERSECURITY',
    label: 'Cybersecurity',
    description: 'Lateral movement, alerts, asset relationships',
    icon: ShieldCheck,
    enabled: true,
  },
  {
    id: 'KNOWLEDGE_GRAPH',
    label: 'Knowledge graph',
    description: 'Documents, chunks, entities for GraphRAG',
    icon: BookOpen,
    enabled: true,
  },
];

interface Props {
  selected: UseCase | null;
  onSelect: (id: UseCase) => void;
}

export default function UseCaseGrid({ selected, onSelect }: Props) {
  return (
    <div className="grid grid-cols-2 gap-2">
      {USE_CASES.map((uc) => {
        const Icon = uc.icon;
        const isSelected = uc.enabled && (selected as string) === uc.id;
        const isDisabled = !uc.enabled;
        return (
          <button
            key={uc.id}
            type="button"
            disabled={isDisabled}
            onClick={() => uc.enabled && onSelect(uc.id as UseCase)}
            className={clsx(
              'flex flex-col items-start gap-2 rounded-xl border bg-tgl-card p-4 text-left transition-all',
              isSelected
                ? 'border-tg-orange ring-1 ring-tg-orange/30'
                : 'border-tgl-border hover:border-tg-orange hover:bg-tgl-bubble',
              isDisabled && 'cursor-not-allowed opacity-50 hover:border-tgl-border hover:bg-tgl-card',
            )}
          >
            <div
              className={clsx(
                'flex h-8 w-8 items-center justify-center rounded-lg',
                isSelected ? 'bg-tgl-chip text-tg-orange' : 'bg-tgl-bubble text-tgl-mute',
              )}
            >
              <Icon size={16} />
            </div>
            <div className="flex w-full items-center justify-between">
              <span className="text-[13.5px] font-semibold text-tgl-ink">{uc.label}</span>
              {!uc.enabled && (
                <span className="rounded-full bg-tgl-bubble px-2 py-0.5 text-[10px] text-tgl-mute">
                  soon
                </span>
              )}
            </div>
            <p className="text-[12px] leading-snug text-tgl-mute">{uc.description}</p>
          </button>
        );
      })}
    </div>
  );
}
