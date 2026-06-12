'use client';

import clsx from 'clsx';
import {
  ArrowRight,
  Check,
  ChevronDown,
  ChevronUp,
  Lightbulb,
  ListChecks,
  Maximize2,
  Network,
  Sparkles,
  Telescope,
  X,
} from 'lucide-react';
import { useState } from 'react';
import type { Assumption, Confidence, Schema, SchemaScore, ValidationResult } from '@/lib/types';

interface Props {
  schema: Schema | null;
  validation: ValidationResult | null;
  score: SchemaScore | null;
  /** Backend-computed composite confidence (preferred over the frontend
   * fallback). Streamed from the SSE `final` event. */
  backendConfidence?: Confidence | null;
}

/**
 * Outcomes-first panel — replaces the "Schema score 98/100" framing.
 *
 * Shows what the graph can actually answer, what assumptions the agent made,
 * and a confidence label. The structural score is available as a disclosure
 * but is no longer the headline.
 */
export default function OutcomesPanel({
  schema,
  validation,
  score,
  backendConfidence,
}: Props) {
  const [techOpen, setTechOpen] = useState(false);
  // Whole panel collapses to a tiny pill by default so the schema graph
  // is fully visible. User clicks the pill to expand the breakdown.
  const [panelExpanded, setPanelExpanded] = useState(false);

  if (!schema) return null;

  // Prefer backend-computed confidence (real signal). Fall back to the
  // conservative frontend derivation only when the SSE event didn't ship
  // a confidence value (e.g. legacy turns).
  const confidence = backendConfidence ?? deriveConfidence(score, validation);
  const answerable = validation?.answerable_questions ?? [];
  const unanswerable = validation?.unanswerable_questions ?? [];
  const assumptions = schema.assumptions ?? [];
  const rationaleBullets = schema.design_rationale?.bullets ?? [];
  const recEntities = schema.recommendation?.entities ?? [];
  const expectedOutcomes = schema.recommendation?.expected_outcomes ?? [];
  const futureEnhancements = schema.recommendation?.future_enhancements ?? [];

  // ----- compact pill mode (default) -----
  if (!panelExpanded) {
    const answered = answerable.length;
    const total = answered + unanswerable.length;
    return (
      <button
        type="button"
        onClick={() => setPanelExpanded(true)}
        className="flex items-center gap-2.5 rounded-full border border-tg-line bg-tg-card px-3 py-2 shadow-card transition-colors hover:border-tg-purple hover:bg-tg-hover"
        title="Show full outcomes breakdown"
      >
        <Sparkles size={13} className="text-tg-purple-500" />
        <span className="text-[11.5px] text-tg-mute">Confidence</span>
        <span className={clsx('text-[12px] font-semibold', confidenceColor(confidence))}>
          {confidence}
        </span>
        <ConfidenceBadge confidence={confidence} />
        {total > 0 && (
          <span className="rounded-full bg-tg-hover px-2 py-0.5 text-[10.5px] font-medium text-tg-ink">
            {answered}/{total} answered
          </span>
        )}
        <Maximize2 size={11} className="text-tg-mute" />
      </button>
    );
  }

  // ----- full breakdown -----
  return (
    <div className="rounded-xl border border-tg-line bg-tg-card shadow-card">
      {/* Confidence header */}
      <div className="flex items-center gap-2.5 px-4 py-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-full bg-tg-purple-100">
          <Sparkles size={14} className="text-tg-purple-500" />
        </div>
        <div className="flex-1">
          <div className="text-[10.5px] uppercase tracking-wide text-tg-mute">Confidence</div>
          <div className={clsx('text-[15px] font-semibold', confidenceColor(confidence))}>
            {confidence}
          </div>
        </div>
        <ConfidenceBadge confidence={confidence} />
        <button
          type="button"
          onClick={() => setPanelExpanded(false)}
          className="flex h-7 w-7 items-center justify-center rounded-md text-tg-mute hover:bg-tg-hover hover:text-tg-ink"
          title="Collapse to pill"
        >
          <ChevronUp size={13} />
        </button>
      </div>

      {/* Design Rationale (Behavior 6) — only section open by default */}
      {rationaleBullets.length > 0 && (
        <CollapsibleSection
          icon={<Network size={11} className="text-tg-purple-500" />}
          label="Design rationale"
          count={rationaleBullets.length}
          defaultOpen
        >
          <ul className="space-y-1">
            {rationaleBullets.map((b, i) => (
              <li
                key={i}
                className="flex items-start gap-1.5 text-[11.5px] leading-snug text-tg-ink"
              >
                <span className="mt-1 inline-block h-1 w-1 shrink-0 rounded-full bg-tg-purple-500" />
                <span>{b}</span>
              </li>
            ))}
          </ul>
        </CollapsibleSection>
      )}

      {/* Recommended Entities (Behavior 8) — collapsed */}
      {recEntities.length > 0 && (
        <CollapsibleSection
          icon={<ListChecks size={11} className="text-tg-purple-500" />}
          label="Recommended entities"
          count={recEntities.length}
        >
          <ul className="space-y-1">
            {recEntities.map((e, i) => (
              <li
                key={i}
                className="flex items-start gap-2 text-[11.5px] leading-snug"
              >
                <span className="font-medium text-tg-ink">{e.name}</span>
                {e.one_liner && (
                  <span className="text-tg-mute">— {e.one_liner}</span>
                )}
              </li>
            ))}
          </ul>
        </CollapsibleSection>
      )}

      {/* Expected Outcomes (Behavior 8) — collapsed */}
      {expectedOutcomes.length > 0 && (
        <CollapsibleSection
          icon={<ArrowRight size={11} className="text-green-400" />}
          label="Expected outcomes"
          count={expectedOutcomes.length}
        >
          <ul className="space-y-1">
            {expectedOutcomes.map((o, i) => (
              <li
                key={i}
                className="flex items-start gap-1.5 text-[11.5px] leading-snug text-tg-ink"
              >
                <Check size={11} className="mt-0.5 shrink-0 text-green-400" />
                <span>{o}</span>
              </li>
            ))}
          </ul>
        </CollapsibleSection>
      )}

      {/* Questions answered (Behavior 7) — collapsed */}
      {(answerable.length > 0 || unanswerable.length > 0) && (
        <CollapsibleSection
          icon={<Check size={11} className="text-green-400" />}
          label="Questions answered"
          count={`${answerable.length}/${answerable.length + unanswerable.length}`}
        >
          <ul className="space-y-1">
            {answerable.map((q, i) => (
              <li key={`a-${i}`} className="flex items-start gap-1.5 text-[11.5px] leading-snug text-tg-ink">
                <Check size={11} className="mt-0.5 shrink-0 text-green-400" />
                <span>{q}</span>
              </li>
            ))}
            {unanswerable.map((q, i) => (
              <li
                key={`u-${i}`}
                className="flex items-start gap-1.5 text-[11.5px] leading-snug text-tg-mute"
                title="This question can't be answered with the current schema"
              >
                <X size={11} className="mt-0.5 shrink-0 text-tg-mute opacity-60" />
                <span className="line-through opacity-70">{q}</span>
              </li>
            ))}
          </ul>
        </CollapsibleSection>
      )}

      {/* Assumptions — collapsed */}
      {assumptions.length > 0 && (
        <CollapsibleSection
          icon={<Lightbulb size={11} className="text-tg-orange" />}
          label="Assumptions"
          count={assumptions.length}
        >
          <ul className="space-y-1.5">
            {assumptions.map((a, i) => (
              <AssumptionRow key={i} assumption={a} />
            ))}
          </ul>
        </CollapsibleSection>
      )}

      {/* Potential Future Enhancements (Behavior 8) — collapsed */}
      {futureEnhancements.length > 0 && (
        <CollapsibleSection
          icon={<Telescope size={11} className="text-tg-orange" />}
          label="Future enhancements"
          count={futureEnhancements.length}
        >
          <ul className="space-y-1">
            {futureEnhancements.map((f, i) => (
              <li
                key={i}
                className="flex items-start gap-1.5 text-[11.5px] leading-snug text-tg-mute"
              >
                <span className="mt-1 inline-block h-1 w-1 shrink-0 rounded-full bg-tg-orange" />
                <span>{f}</span>
              </li>
            ))}
          </ul>
        </CollapsibleSection>
      )}

      {/* Technical score — collapsible disclosure */}
      {score && (
        <div className="border-t border-tg-line">
          <button
            type="button"
            onClick={() => setTechOpen(!techOpen)}
            className="flex w-full items-center justify-between px-4 py-2.5 text-tg-mute hover:bg-tg-hover"
          >
            <span className="text-[11px] font-medium">
              Technical score: {score.total}/100
            </span>
            <ChevronDown
              size={12}
              style={{
                transform: techOpen ? 'rotate(180deg)' : 'rotate(0)',
                transition: 'transform 0.15s',
              }}
            />
          </button>
          {techOpen && (
            <div className="border-t border-tg-line px-4 py-3 text-[11px]">
              <div className="space-y-1">
                {Object.entries(score.breakdown ?? {})
                  .sort((a, b) => b[1] - a[1])
                  .slice(0, 8)
                  .map(([k, v]) => (
                    <div key={k} className="flex items-center justify-between gap-2">
                      <span className="text-tg-mute">{labelize(k)}</span>
                      <span
                        className={clsx(
                          'font-medium tabular-nums',
                          v >= 80 ? 'text-green-400' : v >= 50 ? 'text-amber-400' : 'text-red-400',
                        )}
                      >
                        {v}
                      </span>
                    </div>
                  ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// -------------------- subviews --------------------

function CollapsibleSection({
  icon,
  label,
  count,
  defaultOpen = false,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  count?: number | string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-t border-tg-line">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-1.5 px-4 py-2 text-tg-mute hover:bg-tg-hover"
      >
        {icon}
        <span className="flex-1 text-left text-[10.5px] font-semibold uppercase tracking-wide">
          {label}
        </span>
        {count !== undefined && count !== null && count !== '' && (
          <span className="rounded-full bg-tg-hover px-1.5 py-0 text-[10px] font-medium text-tg-ink">
            {count}
          </span>
        )}
        <ChevronDown
          size={12}
          style={{
            transform: open ? 'rotate(180deg)' : 'rotate(0)',
            transition: 'transform 0.15s',
          }}
        />
      </button>
      {open && <div className="px-4 pb-3">{children}</div>}
    </div>
  );
}

function AssumptionRow({ assumption }: { assumption: Assumption }) {
  const [open, setOpen] = useState(false);
  return (
    <li>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-start gap-1.5 text-left text-[11.5px] leading-snug text-tg-ink hover:text-tg-purple-700"
      >
        <span
          className={clsx(
            'mt-1 inline-block h-1.5 w-1.5 shrink-0 rounded-full',
            assumption.confidence === 'high'
              ? 'bg-green-400'
              : assumption.confidence === 'medium'
                ? 'bg-amber-400'
                : 'bg-red-400',
          )}
          title={`${assumption.confidence} confidence`}
        />
        <span className="flex-1">{assumption.text}</span>
      </button>
      {open && assumption.evidence && (
        <div className="ml-3 mt-1 border-l border-tg-line pl-2 text-[10.5px] italic text-tg-mute">
          {assumption.evidence}
        </div>
      )}
    </li>
  );
}

function ConfidenceBadge({ confidence }: { confidence: Confidence }) {
  const bg =
    confidence === 'High' ? '#16A34A' : confidence === 'Medium' ? '#F4B860' : '#DC2626';
  return (
    <div
      className="flex h-7 min-w-[28px] items-center justify-center rounded-md px-2 text-[11px] font-bold text-white"
      style={{ background: bg }}
      title={`Agent confidence: ${confidence}`}
    >
      {confidence === 'High' ? 'H' : confidence === 'Medium' ? 'M' : 'L'}
    </div>
  );
}

// -------------------- helpers --------------------

/**
 * Frontend-side fallback for confidence until the backend ships
 * `compute_confidence` in P7. Conservative: defaults to Medium when signals
 * are mixed, requires strong outcome coverage AND a healthy structural score
 * to claim High.
 */
function deriveConfidence(
  score: SchemaScore | null,
  validation: ValidationResult | null,
): Confidence {
  if (!score && !validation) return 'Medium';
  const answerable = validation?.answerable_questions.length ?? 0;
  const unanswerable = validation?.unanswerable_questions.length ?? 0;
  const total = answerable + unanswerable;
  const coverage = total > 0 ? answerable / total : 0;
  const structural = score?.total ?? 0;

  if (coverage >= 0.8 && structural >= 75) return 'High';
  if (coverage >= 0.5 && structural >= 50) return 'Medium';
  if (structural >= 75 && total === 0) return 'Medium'; // structural-only signal
  if (structural < 50 || coverage < 0.3) return 'Low';
  return 'Medium';
}

function confidenceColor(confidence: Confidence): string {
  return confidence === 'High'
    ? 'text-green-400'
    : confidence === 'Medium'
      ? 'text-amber-400'
      : 'text-red-400';
}

function labelize(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

