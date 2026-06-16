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
    label: 'Fraud Detection',
    description: 'Detect shared devices, IPs, and identity signals to identify coordinated attacks.',
    icon: ShieldAlert,
    enabled: true,
  },
  {
    id: 'CUSTOMER_360',
    label: 'Customer 360',
    description: 'Unified view of customer interactions and multi-channel relationship mapping.',
    icon: Users,
    enabled: true,
  },
  {
    id: 'ENTITY_RESOLUTION',
    label: 'AML',
    description: 'Anti-money laundering transaction flows and suspicious activity monitoring.',
    icon: Sparkles,
    enabled: true,
  },
  {
    id: 'RECOMMENDATION',
    label: 'Recommendation',
    description: 'Users, items, interactions for collaborative filtering and personalization.',
    icon: Database,
    enabled: true,
  },
  {
    id: 'SUPPLY_CHAIN',
    label: 'Supply Chain',
    description: 'Suppliers, shipments, bottleneck analysis across logistics networks.',
    icon: Truck,
    enabled: true,
  },
  {
    id: 'CYBERSECURITY',
    label: 'Cybersecurity',
    description: 'Lateral movement, alerts, and asset relationships across the network.',
    icon: ShieldCheck,
    enabled: true,
  },
  {
    id: 'KNOWLEDGE_GRAPH',
    label: 'Knowledge Graph',
    description: 'Documents, chunks, entities for GraphRAG and retrieval-augmented search.',
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
    <div className="space-y-2">
      {USE_CASES.map((uc) => {
        const isSelected = uc.enabled && (selected as string) === uc.id;
        const isDisabled = !uc.enabled;
        return (
          <button
            key={uc.id}
            type="button"
            disabled={isDisabled}
            onClick={() => uc.enabled && onSelect(uc.id as UseCase)}
            className={clsx(
              'w-full rounded-lg border bg-tgl-card p-3 text-left transition-colors',
              isSelected
                ? 'border-tg-orange ring-1 ring-tg-orange/30'
                : 'border-tgl-border hover:border-tg-orange hover:bg-tgl-bubble',
              isDisabled && 'cursor-not-allowed opacity-50',
            )}
          >
            <div className="text-[12.5px] font-semibold text-tgl-ink">{uc.label}</div>
            <p className="mt-1 text-[11.5px] leading-snug text-tgl-mute">{uc.description}</p>
          </button>
        );
      })}
    </div>
  );
}
