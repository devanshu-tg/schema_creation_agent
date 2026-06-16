'use client';

import clsx from 'clsx';
import { ChevronDown, Eye, Globe, LayoutGrid, Plus, Redo, Rocket, Sparkles, Undo } from 'lucide-react';
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
      {/* Canvas action bar — undo/redo + Create Edge / Create Vertex + view controls */}
      <div className="flex items-center justify-end gap-2 border-b border-tg-border bg-tg-panel px-4 py-2">
        <div className="flex items-center gap-1 rounded-md border border-tg-border bg-tg-card px-1 py-0.5">
          <button type="button" className="rounded p-1 text-tg-mute hover:bg-tg-hover hover:text-tg-ink" title="Undo">
            <Undo size={13} />
          </button>
          <button type="button" className="rounded p-1 text-tg-mute hover:bg-tg-hover hover:text-tg-ink" title="Redo">
            <Redo size={13} />
          </button>
        </div>
        <button
          type="button"
          className="flex items-center gap-1 rounded-md border border-tg-border bg-tg-card px-2.5 py-1.5 text-[11.5px] font-medium text-tg-ink hover:bg-tg-hover"
        >
          <Plus size={12} /> Create Edge
        </button>
        <button
          type="button"
          className="flex items-center gap-1 rounded-md border border-tg-border bg-tg-card px-2.5 py-1.5 text-[11.5px] font-medium text-tg-ink hover:bg-tg-hover"
        >
          <Plus size={12} /> Create Vertex
        </button>
        <button type="button" className="rounded-md border border-tg-border bg-tg-card p-1.5 text-tg-mute hover:bg-tg-hover" title="Global">
          <Globe size={13} />
        </button>
        <button type="button" className="rounded-md border border-tg-border bg-tg-card p-1.5 text-tg-mute hover:bg-tg-hover" title="View">
          <LayoutGrid size={13} />
        </button>
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

        {/* Bottom status bar — READY TO MAP + counts + version (matches TG Cloud) */}
        {schema && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-center justify-between border-t border-tg-border bg-tg-panel/90 px-4 py-2 text-[11px] text-tg-mute backdrop-blur">
            <div className="flex items-center gap-1.5">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-tg-orange" />
              <span className="font-semibold text-tg-ink">READY TO MAP</span>
              <span className="ml-3">
                {schema.vertices.length} Vertices / {schema.edges.length} Edges
              </span>
            </div>
            <div className="flex items-center gap-3">
              <span>Last updated just now</span>
              <span className="text-tg-subtle">v1.4.0-propose</span>
            </div>
          </div>
        )}

        {/* Relationships summary legend — pointer-events-auto inside */}
        {schema && schema.edges.length > 0 && (
          <div className="pointer-events-auto absolute bottom-12 right-5 max-w-[220px] rounded-lg border border-tg-border bg-tg-card p-3 shadow-card">
            <div className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-tg-mute">
              Relationships Summary
            </div>
            <ul className="space-y-1">
              {schema.edges.slice(0, 5).map((e, i) => (
                <li key={i} className="flex items-center justify-between text-[11px] text-tg-ink">
                  <span className="truncate">{e.name}</span>
                  <span className="ml-2 inline-block h-px w-10 bg-tg-orange/60" />
                </li>
              ))}
            </ul>
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
