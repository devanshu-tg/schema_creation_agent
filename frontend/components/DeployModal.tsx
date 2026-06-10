'use client';

import clsx from 'clsx';
import {
  AlertCircle,
  Check,
  Clock,
  ExternalLink,
  Eye,
  FileSearch,
  ListChecks,
  Lightbulb,
  Rocket,
  Sparkles,
  Telescope,
  Wand2,
  X,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import type {
  DeployCountEvent,
  DeployDoneEvent,
  DeployPhase,
  DeployStepEvent,
  Schema,
  SchemaScore,
  ValidationResult,
} from '@/lib/types';

export type DeployModalMode = 'review' | 'preview' | 'deploy';

interface Props {
  workspaceId: string;
  csvFilename: string;
  mode: DeployModalMode;
  onClose: () => void;
  // Optional context for the new 'review' mode + next-steps panel.
  // The modal still works without these (falls back to preview/deploy only).
  schema?: Schema | null;
  validation?: ValidationResult | null;
  score?: SchemaScore | null;
  tgConsoleUrl?: string;
  onOpenStarterQueries?: () => void;
  onLoadMoreData?: () => void;
  onDeployCompleted?: (graphName: string, counts: Record<string, number | null>) => void;
}

interface StepRecord {
  phase: DeployPhase;
  name: string;
  status: 'running' | 'ok' | 'failed' | 'info';
  summary: string;
}

export default function DeployModal({
  workspaceId,
  csvFilename,
  mode: initialMode,
  onClose,
  schema = null,
  validation = null,
  score = null,
  tgConsoleUrl,
  onOpenStarterQueries,
  onLoadMoreData,
  onDeployCompleted,
}: Props) {
  const [mode, setMode] = useState<DeployModalMode>(initialMode);
  // After a successful deploy, the user can request a follow-up data
  // load or starter-query generation. These are stubbed for now —
  // future-D4 lights them up.
  const [loadDataAfter, setLoadDataAfter] = useState<boolean>(false);
  const [previewText, setPreviewText] = useState<string | null>(null);
  const [previewGraph, setPreviewGraph] = useState<string>('');
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const [steps, setSteps] = useState<StepRecord[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [done, setDone] = useState<DeployDoneEvent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deploying, setDeploying] = useState(false);
  const cancelRef = useRef<(() => void) | null>(null);

  // ----- preview loader -----
  useEffect(() => {
    if (mode !== 'preview') return;
    if (previewText || previewing) return;
    setPreviewing(true);
    setPreviewError(null);
    api
      .previewDeploy(workspaceId, csvFilename)
      .then((p) => {
        setPreviewText(p.dry_run_plan);
        setPreviewGraph(p.graph_name);
      })
      .catch((e: Error) => setPreviewError(e.message))
      .finally(() => setPreviewing(false));
  }, [mode, workspaceId, csvFilename, previewText, previewing]);

  // ----- deploy stream -----
  const startDeploy = () => {
    setMode('deploy');
    setSteps([]);
    setCounts({});
    setDone(null);
    setError(null);
    setDeploying(true);

    cancelRef.current = api.deployStream(
      workspaceId,
      csvFilename,
      {
      onStep: (e) => {
        setSteps((prev) => {
          // Replace the last "running" step with same name if status changed
          const idx = prev.findIndex(
            (s) => s.name === e.name && s.phase === e.phase && s.status === 'running',
          );
          if (idx !== -1 && e.status !== 'running') {
            const next = [...prev];
            next[idx] = e;
            return next;
          }
          return [...prev, e];
        });
      },
      onCount: (e: DeployCountEvent) => {
        setCounts((prev) => ({ ...prev, [e.vertex]: e.count }));
      },
      onDone: (d) => {
        setDone(d);
        setDeploying(false);
        // Echo into chat history so the user has a single timeline.
        onDeployCompleted?.(d.graph_name, d.vertex_counts ?? {});
      },
      onError: (msg) => {
        setError(msg);
        setDeploying(false);
      },
      },
      { loadData: loadDataAfter },
    );
  };

  // Auto-start deploy if modal opened in deploy mode
  useEffect(() => {
    if (initialMode === 'deploy' && !deploying && !done && !error && steps.length === 0) {
      startDeploy();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Cleanup any in-flight stream when modal closes
  useEffect(() => {
    return () => {
      cancelRef.current?.();
    };
  }, []);

  const totalSteps = steps.length;
  const okSteps = steps.filter((s) => s.status === 'ok').length;
  const failedSteps = steps.filter((s) => s.status === 'failed').length;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-[800px] flex-col overflow-hidden rounded-2xl border border-tg-line bg-tg-panel shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-tg-border px-5 py-3.5">
          <div className="flex items-center gap-2.5">
            <div
              className={clsx(
                'flex h-9 w-9 items-center justify-center rounded-lg',
                mode === 'preview'
                  ? 'bg-tg-purple-100'
                  : mode === 'review'
                    ? 'bg-tg-purple-100'
                    : 'bg-tg-purple',
              )}
            >
              {mode === 'preview' ? (
                <Sparkles size={16} className="text-tg-purple-500" />
              ) : mode === 'review' ? (
                <ListChecks size={16} className="text-tg-purple-500" />
              ) : (
                <Rocket size={16} className="text-white" />
              )}
            </div>
            <div>
              <h2 className="text-[14.5px] font-semibold text-tg-ink">
                {mode === 'preview'
                  ? 'Deploy Preview'
                  : mode === 'review'
                    ? 'Review & Deploy'
                    : 'Deploy to TigerGraph'}
              </h2>
              <p className="mt-0.5 text-[11.5px] text-tg-mute">
                {mode === 'preview'
                  ? 'Read-only GSQL plan — nothing happens in TigerGraph until you click Deploy Now.'
                  : mode === 'review'
                    ? 'Confirm the recommendation, then deploy. You can also opt to load data in the same run.'
                    : 'Creating schema in TigerGraph via tigergraph-mcp…'}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-md text-tg-mute hover:bg-tg-card hover:text-tg-ink"
            title="Close"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {mode === 'review' ? (
            <ReviewBody
              schema={schema}
              validation={validation}
              score={score}
              loadDataAfter={loadDataAfter}
              setLoadDataAfter={setLoadDataAfter}
            />
          ) : mode === 'preview' ? (
            <PreviewBody
              loading={previewing}
              text={previewText}
              graphName={previewGraph}
              error={previewError}
            />
          ) : (
            <DeployBody
              steps={steps}
              counts={counts}
              done={done}
              error={error}
              deploying={deploying}
              tgConsoleUrl={tgConsoleUrl}
              onClose={onClose}
              onOpenStarterQueries={onOpenStarterQueries}
              onLoadMoreData={onLoadMoreData}
            />
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 border-t border-tg-border px-5 py-3.5">
          <div className="text-[11.5px] text-tg-mute">
            {mode === 'deploy' && deploying && (
              <span>
                {okSteps}/{totalSteps} steps complete
                {failedSteps > 0 && (
                  <span className="ml-2 text-red-400">· {failedSteps} failed</span>
                )}
              </span>
            )}
            {mode === 'deploy' && done && !error && (
              <span className="text-green-400">✓ Deployment complete</span>
            )}
            {mode === 'deploy' && error && (
              <span className="text-red-400">Error: {error}</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-tg-line bg-tg-card px-3 py-1.5 text-[12.5px] font-medium text-tg-ink hover:bg-tg-hover"
            >
              {mode === 'deploy' && done ? 'Done' : 'Cancel'}
            </button>
            {mode === 'review' && (
              <>
                <button
                  type="button"
                  onClick={() => setMode('preview')}
                  className="rounded-md border border-tg-line bg-tg-card px-3 py-1.5 text-[12.5px] font-medium text-tg-ink hover:bg-tg-hover"
                  title="Inspect the raw GSQL plan"
                >
                  <span className="inline-flex items-center gap-1.5">
                    <Eye size={12} /> Preview GSQL
                  </span>
                </button>
                <button
                  type="button"
                  onClick={startDeploy}
                  className="inline-flex items-center gap-1.5 rounded-md bg-tg-purple px-3 py-1.5 text-[12.5px] font-semibold text-white hover:bg-tg-purple-600"
                >
                  <Rocket size={12} />
                  {loadDataAfter ? 'Deploy & load data' : 'Deploy schema'}
                </button>
              </>
            )}
            {mode === 'preview' && (
              <button
                type="button"
                onClick={startDeploy}
                disabled={!previewText || !!previewError || previewing}
                className={clsx(
                  'inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12.5px] font-semibold transition-colors',
                  !previewText || !!previewError || previewing
                    ? 'cursor-not-allowed bg-tg-card text-tg-subtle'
                    : 'bg-tg-purple text-white hover:bg-tg-purple-600',
                )}
              >
                <Rocket size={12} />
                Deploy Now
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// -------------------- subviews --------------------

function ReviewBody({
  schema,
  validation,
  score,
  loadDataAfter,
  setLoadDataAfter,
}: {
  schema: Schema | null;
  validation: ValidationResult | null;
  score: SchemaScore | null;
  loadDataAfter: boolean;
  setLoadDataAfter: (v: boolean) => void;
}) {
  if (!schema) {
    return (
      <div className="text-[12.5px] text-tg-mute">
        No schema available. Design one first, then come back to deploy.
      </div>
    );
  }
  const entities = schema.recommendation?.entities ?? [];
  const outcomes = schema.recommendation?.expected_outcomes ?? [];
  const future = schema.recommendation?.future_enhancements ?? [];
  const rationale = schema.design_rationale?.bullets ?? [];
  const assumptions = schema.assumptions ?? [];
  const answerable = validation?.answerable_questions ?? [];
  const unanswerable = validation?.unanswerable_questions ?? [];

  return (
    <div className="space-y-4 text-[12px]">
      <div className="grid grid-cols-3 gap-3 rounded-lg border border-tg-line bg-tg-card p-3">
        <div>
          <div className="text-[10px] uppercase tracking-wide text-tg-mute">Vertices</div>
          <div className="text-[18px] font-semibold text-tg-ink">{schema.vertices.length}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wide text-tg-mute">Edges</div>
          <div className="text-[18px] font-semibold text-tg-ink">{schema.edges.length}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wide text-tg-mute">Questions answered</div>
          <div className="text-[18px] font-semibold text-tg-ink">
            {answerable.length}
            <span className="text-tg-mute">
              /{answerable.length + unanswerable.length}
            </span>
          </div>
        </div>
      </div>

      {rationale.length > 0 && (
        <ReviewSection label="Design rationale">
          <ul className="space-y-1">
            {rationale.map((b, i) => (
              <li key={i} className="flex items-start gap-1.5 leading-snug text-tg-ink">
                <span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-tg-purple-500" />
                <span>{b}</span>
              </li>
            ))}
          </ul>
        </ReviewSection>
      )}

      {entities.length > 0 && (
        <ReviewSection label="Recommended entities">
          <ul className="space-y-0.5">
            {entities.map((e, i) => (
              <li key={i} className="flex items-baseline gap-2 leading-snug">
                <span className="font-medium text-tg-ink">{e.name}</span>
                {e.one_liner && <span className="text-tg-mute">— {e.one_liner}</span>}
              </li>
            ))}
          </ul>
        </ReviewSection>
      )}

      {outcomes.length > 0 && (
        <ReviewSection label="Expected outcomes">
          <ul className="space-y-1">
            {outcomes.map((o, i) => (
              <li key={i} className="flex items-start gap-1.5 leading-snug text-tg-ink">
                <Check size={11} className="mt-1 shrink-0 text-green-400" />
                <span>{o}</span>
              </li>
            ))}
          </ul>
        </ReviewSection>
      )}

      {assumptions.length > 0 && (
        <ReviewSection label="Key assumptions">
          <ul className="space-y-1">
            {assumptions.slice(0, 6).map((a, i) => (
              <li key={i} className="flex items-start gap-1.5 leading-snug text-tg-ink">
                <Lightbulb size={11} className="mt-1 shrink-0 text-tg-orange" />
                <span>
                  {a.text}
                  <span className="ml-1 text-tg-mute">({a.confidence})</span>
                </span>
              </li>
            ))}
          </ul>
        </ReviewSection>
      )}

      {future.length > 0 && (
        <ReviewSection label="Deferred / future enhancements">
          <ul className="space-y-1">
            {future.map((f, i) => (
              <li key={i} className="flex items-start gap-1.5 leading-snug text-tg-mute">
                <Telescope size={11} className="mt-1 shrink-0 text-tg-orange" />
                <span>{f}</span>
              </li>
            ))}
          </ul>
        </ReviewSection>
      )}

      {/* Data-load opt-in */}
      <div className="rounded-lg border border-tg-line bg-tg-card p-3">
        <label className="flex cursor-pointer items-start gap-2.5 text-[12.5px] text-tg-ink">
          <input
            type="checkbox"
            checked={loadDataAfter}
            onChange={(e) => setLoadDataAfter(e.target.checked)}
            className="mt-0.5 h-4 w-4 cursor-pointer accent-tg-purple"
          />
          <div>
            <div className="font-medium">
              Also load the uploaded CSV after schema creates
            </div>
            <div className="mt-0.5 text-[11px] text-tg-mute">
              Streams the file into TigerGraph using a generated loading job.
              Unchecked deploys schema only (you can load later).
            </div>
          </div>
        </label>
      </div>

      {score && (
        <div className="text-[11px] text-tg-subtle">
          Technical score: {score.total}/100 · click Deploy to commit.
        </div>
      )}
    </div>
  );
}

function ReviewSection({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1 text-[10.5px] font-semibold uppercase tracking-wide text-tg-mute">
        {label}
      </div>
      <div className="rounded-lg border border-tg-line bg-tg-card p-3 text-[12px]">
        {children}
      </div>
    </div>
  );
}

function PreviewBody({
  loading,
  text,
  graphName,
  error,
}: {
  loading: boolean;
  text: string | null;
  graphName: string;
  error: string | null;
}) {
  if (loading) {
    return (
      <div className="flex items-center justify-center py-12 text-[12.5px] text-tg-mute">
        <span className="animate-pulse">Generating deploy plan…</span>
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-[12.5px] text-red-200">
        <div className="flex items-center gap-2 font-medium">
          <AlertCircle size={13} />
          Couldn&apos;t build the deploy plan
        </div>
        <div className="mt-1.5 text-[11.5px] text-red-300">{error}</div>
        <div className="mt-2.5 text-[11px] text-tg-mute">
          Usually means a required env var (TG_HOST / TG_GRAPHNAME) is missing from{' '}
          <code className="rounded bg-tg-card px-1 py-0.5">.env</code>. Set them and restart the
          backend.
        </div>
      </div>
    );
  }
  return (
    <div>
      {graphName && (
        <div className="mb-3 flex items-center gap-2 text-[11.5px] text-tg-mute">
          <span className="rounded-full bg-tg-purple-100 px-2 py-0.5 font-medium text-tg-purple-700">
            graph: {graphName}
          </span>
          <span>Plan generated. Review then click Deploy Now to execute.</span>
        </div>
      )}
      <pre
        className="max-h-[55vh] overflow-auto rounded-lg border border-tg-line bg-tg-bg p-3 text-[11.5px] leading-relaxed text-tg-ink"
        style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace' }}
      >
        {text}
      </pre>
    </div>
  );
}

function DeployBody({
  steps,
  counts,
  done,
  error,
  deploying,
  tgConsoleUrl,
  onClose,
  onOpenStarterQueries,
  onLoadMoreData,
}: {
  steps: StepRecord[];
  counts: Record<string, number>;
  done: DeployDoneEvent | null;
  error: string | null;
  deploying: boolean;
  tgConsoleUrl?: string;
  onClose: () => void;
  onOpenStarterQueries?: () => void;
  onLoadMoreData?: () => void;
}) {
  // Group steps by phase for nicer rendering
  const grouped = useMemo(() => {
    const out: Record<string, StepRecord[]> = {};
    for (const s of steps) {
      const k = s.phase;
      out[k] = out[k] || [];
      out[k].push(s);
    }
    return out;
  }, [steps]);

  return (
    <div className="space-y-3">
      {error && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-[12.5px] text-red-200">
          <div className="flex items-center gap-2 font-medium">
            <AlertCircle size={13} />
            Deployment failed
          </div>
          <div className="mt-1.5 text-[11.5px] text-red-300">{error}</div>
        </div>
      )}

      {done && !error && (
        <>
          <div className="rounded-lg border border-green-500/40 bg-green-500/10 p-3 text-[12.5px] text-green-200">
            <div className="flex items-center gap-2 font-semibold">
              <Check size={14} />
              Deployment complete — graph: {done.graph_name}
            </div>
            {Object.keys(counts).length > 0 && (
              <div className="mt-2 grid grid-cols-3 gap-1.5 text-[11.5px]">
                {Object.entries(counts).map(([v, c]) => (
                  <div
                    key={v}
                    className="rounded border border-green-500/30 bg-green-500/5 px-2 py-1"
                  >
                    <span className="font-medium text-green-100">{v}</span>
                    <span className="ml-1 text-green-300">= {c.toLocaleString()}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Next-steps panel (Autograph Behavior 9) */}
          <div className="rounded-lg border border-tg-line bg-tg-card p-4">
            <div className="mb-3 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-tg-mute">
              <Sparkles size={11} className="text-tg-purple-500" />
              Next steps
            </div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <NextStepButton
                icon={<Wand2 size={13} />}
                title="Generate starter queries"
                description="LLM-authored GSQL tailored to your schema + business questions."
                onClick={onOpenStarterQueries}
                disabled={!onOpenStarterQueries}
              />
              <NextStepButton
                icon={<FileSearch size={13} />}
                title="Inspect graph in console"
                description="Open the TigerGraph console to explore vertices and edges."
                onClick={tgConsoleUrl ? () => window.open(tgConsoleUrl, '_blank') : undefined}
                disabled={!tgConsoleUrl}
              />
              <NextStepButton
                icon={<Rocket size={13} />}
                title="Back to chat"
                description="Refine the schema, add entities, or ask follow-up questions."
                onClick={onClose}
              />
              <NextStepButton
                icon={<ExternalLink size={13} />}
                title="Re-deploy / load more data"
                description="Re-open the review modal to re-run the deploy with the load-data checkbox."
                onClick={onLoadMoreData}
                disabled={!onLoadMoreData}
              />
            </div>
          </div>
        </>
      )}

      {steps.length === 0 && deploying && (
        <div className="flex items-center justify-center py-12 text-[12.5px] text-tg-mute">
          <span className="animate-pulse">Connecting to tigergraph-mcp…</span>
        </div>
      )}

      {Object.entries(grouped).map(([phase, phaseSteps]) => (
        <div key={phase}>
          <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-tg-subtle">
            {phaseLabel(phase as DeployPhase)}
          </div>
          <div className="space-y-1">
            {phaseSteps.map((s, i) => (
              <StepLine key={`${phase}-${i}-${s.name}`} step={s} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function NextStepButton({
  icon,
  title,
  description,
  onClick,
  disabled,
  soonLabel,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  onClick?: () => void;
  disabled?: boolean;
  soonLabel?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || !onClick}
      className={clsx(
        'flex items-start gap-2 rounded-lg border px-3 py-2.5 text-left transition-colors',
        disabled || !onClick
          ? 'cursor-not-allowed border-tg-line bg-tg-card opacity-60'
          : 'border-tg-line bg-tg-card hover:border-tg-purple hover:bg-tg-hover',
      )}
    >
      <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-tg-hover text-tg-purple-500">
        {icon}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[12px] font-semibold text-tg-ink">{title}</span>
          {soonLabel && (
            <span className="rounded-full bg-tg-hover px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-tg-mute">
              {soonLabel}
            </span>
          )}
        </div>
        <p className="mt-0.5 text-[11px] leading-snug text-tg-mute">{description}</p>
      </div>
    </button>
  );
}

function StepLine({ step }: { step: StepRecord }) {
  const icon =
    step.status === 'running' ? (
      <Clock size={12} className="animate-pulse text-tg-purple-500" />
    ) : step.status === 'ok' ? (
      <Check size={12} className="text-green-400" />
    ) : step.status === 'failed' ? (
      <X size={12} className="text-red-400" />
    ) : (
      <Sparkles size={12} className="text-tg-mute" />
    );

  return (
    <div className="flex items-start gap-2 text-[11.5px]">
      <span className="mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center">
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        {step.name && <span className="font-medium text-tg-ink">{step.name}</span>}
        {step.summary && (
          <span className="ml-1.5 truncate text-tg-mute" title={step.summary}>
            {step.summary}
          </span>
        )}
      </div>
    </div>
  );
}

function phaseLabel(phase: DeployPhase): string {
  switch (phase) {
    case 'spawn':
      return 'MCP session';
    case 'validate':
      return 'Validation';
    case 'drop':
      return 'Cleanup';
    case 'drop_query':
      return 'Cleanup (queries)';
    case 'vertex':
      return 'Vertices';
    case 'edge':
      return 'Edges';
    case 'graph':
      return 'Graph';
    case 'verify':
      return 'Verification';
    case 'loading_job':
      return 'Loading job';
    case 'run_load':
      return 'Data load';
    case 'counts':
      return 'Row counts';
    default:
      return phase;
  }
}
