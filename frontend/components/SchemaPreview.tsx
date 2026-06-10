'use client';

import clsx from 'clsx';
import { ChevronDown, Eye, Rocket, Sparkles } from 'lucide-react';
import { useState } from 'react';
import type { CriticReview, Schema, SchemaScore, ValidationResult } from '@/lib/types';
import OutcomesPanel from './OutcomesPanel';
import SchemaGraph from './SchemaGraph';

interface Props {
  schema: Schema | null;
  validation: ValidationResult | null;
  score: SchemaScore | null;
  critic: CriticReview | null;
  confidence?: 'High' | 'Medium' | 'Low' | null;
  workspaceLabel: string;
  onGenerate: () => void;
  busy: boolean;
  hasData: boolean;
  onPreviewDeploy?: () => void;
  onDeployNow?: () => void;
}

export default function SchemaPreview({
  schema,
  validation,
  score,
  critic,
  confidence = null,
  workspaceLabel,
  onGenerate,
  busy,
  hasData,
  onPreviewDeploy,
  onDeployNow,
}: Props) {
  const [reviewOpen, setReviewOpen] = useState(false);

  return (
    <div className="relative flex h-full flex-1 flex-col bg-tg-panel">
      {/* Top toolbar — minimal, just the workspace label */}
      <div className="flex items-center justify-between border-b border-tg-border bg-tg-panel px-4 py-2.5">
        <div className="flex items-center gap-2 text-[12.5px] text-tg-mute">
          <span className="h-1.5 w-1.5 rounded-full bg-green-500" />
          <span className="font-medium text-tg-ink">{workspaceLabel}</span>
        </div>
        <div className="text-[11px] text-tg-subtle">
          {schema
            ? `${schema.vertices.length} vertices · ${schema.edges.length} edges`
            : 'No schema yet'}
        </div>
      </div>

      {/* Canvas area */}
      <div className="relative flex-1">
        {schema ? (
          <SchemaGraph schema={schema} />
        ) : (
          <EmptyState onGenerate={onGenerate} busy={busy} hasData={hasData} />
        )}

        {/* Outcomes panel — confidence + what-can-this-answer + assumptions.
            Capped to viewport height and scrollable so a schema with many
            recommended entities / outcomes doesn't push the deploy buttons
            below the fold. */}
        {schema && (
          <div className="absolute left-5 top-5 flex max-h-[calc(100vh-140px)] w-[320px] flex-col gap-2 overflow-y-auto pr-1">
            <OutcomesPanel
              schema={schema}
              validation={validation}
              score={score}
              backendConfidence={confidence}
            />

            {/* Deploy actions */}
            {(onPreviewDeploy || onDeployNow) && (
              <div className="flex gap-2">
                {onPreviewDeploy && (
                  <button
                    type="button"
                    onClick={onPreviewDeploy}
                    disabled={busy}
                    className={clsx(
                      'flex flex-1 items-center justify-center gap-1.5 rounded-lg border px-3 py-2 text-[12px] font-medium transition-colors',
                      busy
                        ? 'cursor-not-allowed border-tg-line bg-tg-card text-tg-subtle'
                        : 'border-tg-line bg-tg-card text-tg-ink hover:border-tg-purple hover:bg-tg-hover',
                    )}
                    title="See the deploy plan without touching TigerGraph"
                  >
                    <Eye size={12} />
                    Preview Plan
                  </button>
                )}
                {onDeployNow && (
                  <button
                    type="button"
                    onClick={onDeployNow}
                    disabled={busy}
                    className={clsx(
                      'flex flex-1 items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-[12px] font-semibold transition-colors',
                      busy
                        ? 'cursor-not-allowed bg-tg-card text-tg-subtle'
                        : 'bg-tg-purple text-white shadow-card hover:bg-tg-purple-600',
                    )}
                    title="Push the schema to your TigerGraph instance"
                  >
                    <Rocket size={12} />
                    Deploy Now
                  </button>
                )}
              </div>
            )}

            {critic && (
              <div className="overflow-hidden rounded-xl border border-tg-line bg-tg-card shadow-card">
                <button
                  type="button"
                  onClick={() => setReviewOpen(!reviewOpen)}
                  className="flex w-full items-center justify-between px-4 py-2.5 text-tg-ink hover:bg-tg-hover"
                >
                  <div className="flex items-center gap-2">
                    <Sparkles size={12} className="text-tg-orange" />
                    <span className="text-[12px] font-medium">Gemini Review</span>
                  </div>
                  <ChevronDown
                    size={13}
                    className="text-tg-mute"
                    style={{
                      transform: reviewOpen ? 'rotate(180deg)' : 'rotate(0)',
                      transition: 'transform 0.15s',
                    }}
                  />
                </button>
                {reviewOpen && (
                  <div className="border-t border-tg-line px-4 py-3 text-[11.5px] text-tg-ink">
                    <p className="mb-3 leading-snug">{critic.overall_judgment}</p>
                    {critic.strengths.length > 0 && (
                      <>
                        <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-green-400">
                          Strengths
                        </div>
                        <ul className="mb-3 space-y-1">
                          {critic.strengths.slice(0, 3).map((s, i) => (
                            <li key={i} className="leading-snug text-tg-mute">
                              <span className="text-green-400">+ </span>
                              {s}
                            </li>
                          ))}
                        </ul>
                      </>
                    )}
                    {critic.improvements.length > 0 && (
                      <>
                        <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-amber-400">
                          Improvements
                        </div>
                        <ul className="mb-3 space-y-1">
                          {critic.improvements.slice(0, 3).map((s, i) => (
                            <li key={i} className="leading-snug text-tg-mute">
                              <span className="text-amber-400">→ </span>
                              {s}
                            </li>
                          ))}
                        </ul>
                      </>
                    )}
                    {critic.next_step_suggestion && (
                      <div className="mt-2 rounded-md bg-tg-purple-100 p-2 text-[11px] text-tg-purple-700">
                        <div className="mb-0.5 font-semibold">Next step:</div>
                        {critic.next_step_suggestion}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// -------------------- helpers --------------------

function EmptyState({
  onGenerate,
  busy,
  hasData,
}: {
  onGenerate: () => void;
  busy: boolean;
  hasData: boolean;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center px-8 text-center">
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-tg-purple-100">
        <Sparkles size={22} className="text-tg-purple-500" />
      </div>
      <h2 className="text-[16px] font-semibold text-tg-ink">
        Graph preview will appear here.
      </h2>
      <p className="mt-1 max-w-md text-[12.5px] leading-relaxed text-tg-mute">
        Connect a data source and Savanna AI will analyze and identify relationships, and generate
        an editable graph schema.
      </p>
      <button
        type="button"
        onClick={onGenerate}
        disabled={busy || !hasData}
        className={clsx(
          'mt-5 inline-flex items-center gap-2 rounded-lg px-4 py-2 text-[13px] font-medium transition-colors',
          busy || !hasData
            ? 'cursor-not-allowed bg-tg-card text-tg-subtle'
            : 'bg-tg-purple text-white shadow-card hover:bg-tg-purple-600',
        )}
      >
        <Sparkles size={14} />
        {busy ? 'Generating…' : 'Generate Graph From Data'}
      </button>
    </div>
  );
}
