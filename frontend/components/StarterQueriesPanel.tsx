'use client';

import clsx from 'clsx';
import {
  AlertCircle,
  Check,
  ChevronDown,
  Loader2,
  Rocket,
  Wand2,
  X,
} from 'lucide-react';
import { useState } from 'react';
import { api } from '@/lib/api';
import type { StarterQueryItem } from '@/lib/types';

interface Props {
  workspaceId: string;
  csvFilename: string;
  onClose: () => void;
  onQueriesGenerated?: (count: number, validated: number) => void;
  onQueryInstalled?: (queryName: string) => void;
}

type InstallState = 'idle' | 'installing' | 'ok' | 'failed';

export default function StarterQueriesPanel({
  workspaceId,
  csvFilename,
  onClose,
  onQueriesGenerated,
  onQueryInstalled,
}: Props) {
  const [loading, setLoading] = useState(false);
  const [queries, setQueries] = useState<StarterQueryItem[]>([]);
  const [graphName, setGraphName] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [installStatus, setInstallStatus] = useState<
    Record<string, { state: InstallState; message?: string }>
  >({});

  const generate = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.generateStarterQueries(workspaceId, csvFilename);
      setQueries(res.queries);
      setGraphName(res.graph_name);
      const validated = res.queries.filter((q) => q.validated).length;
      onQueriesGenerated?.(res.queries.length, validated);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const installOne = async (q: StarterQueryItem) => {
    setInstallStatus((p) => ({ ...p, [q.name]: { state: 'installing' } }));
    try {
      const res = await api.installStarterQuery(workspaceId, q.name, q.gsql);
      setInstallStatus((p) => ({
        ...p,
        [q.name]: {
          state: res.ok ? 'ok' : 'failed',
          message: res.summary || res.error || undefined,
        },
      }));
      if (res.ok) onQueryInstalled?.(q.name);
    } catch (e) {
      setInstallStatus((p) => ({
        ...p,
        [q.name]: {
          state: 'failed',
          message: e instanceof Error ? e.message : String(e),
        },
      }));
    }
  };

  const validatedCount = queries.filter((q) => q.validated).length;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-[820px] flex-col overflow-hidden rounded-2xl border border-tg-line bg-tg-panel shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-tg-border px-5 py-3.5">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-tg-purple-100">
              <Wand2 size={16} className="text-tg-purple-500" />
            </div>
            <div>
              <h2 className="text-[14.5px] font-semibold text-tg-ink">
                Starter GSQL Queries
              </h2>
              <p className="mt-0.5 text-[11.5px] text-tg-mute">
                {queries.length > 0
                  ? `${validatedCount}/${queries.length} dry-run validated · graph: ${graphName}`
                  : 'Gemini will generate queries tailored to your schema and business questions.'}
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
          {loading && (
            <div className="flex items-center justify-center gap-2 py-12 text-[12.5px] text-tg-mute">
              <Loader2 className="animate-spin" size={14} />
              Generating + dry-run validating queries against TigerGraph…
            </div>
          )}

          {!loading && queries.length === 0 && !error && (
            <div className="flex flex-col items-center justify-center gap-3 py-12 text-center text-[12.5px] text-tg-mute">
              <Wand2 size={28} className="text-tg-purple-500" />
              <p className="max-w-md leading-relaxed">
                Gemini will write 5-8 starter queries from your schema and
                target questions, then dry-run each one (INTERPRET QUERY) to
                catch syntax issues before you install.
              </p>
              <button
                type="button"
                onClick={generate}
                className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-tg-purple px-3 py-2 text-[12.5px] font-semibold text-white hover:bg-tg-purple-600"
              >
                <Rocket size={12} />
                Generate starter queries
              </button>
            </div>
          )}

          {error && (
            <div className="rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-[12.5px] text-red-200">
              <div className="flex items-center gap-2 font-medium">
                <AlertCircle size={13} />
                Couldn&apos;t generate queries
              </div>
              <div className="mt-1.5 text-[11.5px] text-red-300">{error}</div>
            </div>
          )}

          {queries.length > 0 && (
            <div className="space-y-3">
              {queries.map((q) => (
                <QueryCard
                  key={q.name}
                  query={q}
                  installState={installStatus[q.name]?.state ?? 'idle'}
                  installMessage={installStatus[q.name]?.message}
                  onInstall={() => installOne(q)}
                />
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 border-t border-tg-border px-5 py-3.5">
          <div className="text-[11.5px] text-tg-mute">
            {queries.length > 0 ? `${validatedCount} ready to install` : ''}
          </div>
          <div className="flex items-center gap-2">
            {queries.length > 0 && (
              <button
                type="button"
                onClick={generate}
                disabled={loading}
                className="rounded-md border border-tg-line bg-tg-card px-3 py-1.5 text-[12.5px] font-medium text-tg-ink hover:bg-tg-hover"
              >
                Regenerate
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-tg-line bg-tg-card px-3 py-1.5 text-[12.5px] font-medium text-tg-ink hover:bg-tg-hover"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function QueryCard({
  query,
  installState,
  installMessage,
  onInstall,
}: {
  query: StarterQueryItem;
  installState: InstallState;
  installMessage?: string;
  onInstall: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const installed = installState === 'ok';
  const installing = installState === 'installing';

  return (
    <div
      className={clsx(
        'overflow-hidden rounded-lg border bg-tg-card',
        query.validated ? 'border-tg-line' : 'border-amber-400/40',
      )}
    >
      <div className="flex items-start justify-between gap-3 px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-medium text-tg-ink text-[13px]">{query.name}</span>
            {query.validated ? (
              <span
                className="inline-flex items-center gap-1 rounded-full bg-green-500/10 px-2 py-0.5 text-[10px] font-medium text-green-400"
                title="Dry-run passed"
              >
                <Check size={10} /> validated
              </span>
            ) : (
              <span
                className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-300"
                title={query.validation_error || 'Dry-run failed'}
              >
                <AlertCircle size={10} /> needs review
              </span>
            )}
          </div>
          <p className="mt-1 text-[12px] leading-snug text-tg-ink">
            {query.description}
          </p>
          {query.business_question && (
            <p className="mt-0.5 text-[11px] italic text-tg-mute">
              Answers: {query.business_question}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            onClick={onInstall}
            disabled={installed || installing}
            className={clsx(
              'inline-flex items-center gap-1 rounded-md px-2.5 py-1.5 text-[12px] font-medium',
              installed
                ? 'cursor-not-allowed bg-green-500/20 text-green-300'
                : installing
                  ? 'cursor-not-allowed bg-tg-card text-tg-subtle'
                  : query.validated
                    ? 'bg-tg-purple text-white hover:bg-tg-purple-600'
                    : 'bg-amber-500/20 text-amber-200 hover:bg-amber-500/30',
            )}
          >
            {installing && <Loader2 className="animate-spin" size={11} />}
            {installed && <Check size={11} />}
            {installed ? 'Installed' : installing ? 'Installing' : 'Install'}
          </button>
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            className="flex h-7 w-7 items-center justify-center rounded-md text-tg-mute hover:bg-tg-hover hover:text-tg-ink"
            title={expanded ? 'Hide GSQL' : 'Show GSQL'}
          >
            <ChevronDown
              size={13}
              style={{
                transform: expanded ? 'rotate(180deg)' : 'rotate(0)',
                transition: 'transform 0.15s',
              }}
            />
          </button>
        </div>
      </div>

      {installState === 'failed' && installMessage && (
        <div className="border-t border-red-500/30 bg-red-500/5 px-4 py-2 text-[11px] text-red-300">
          Install failed: {installMessage}
        </div>
      )}
      {!query.validated && query.validation_error && (
        <div className="border-t border-amber-500/30 bg-amber-500/5 px-4 py-2 text-[11px] text-amber-200">
          Dry-run error: {query.validation_error}
        </div>
      )}

      {expanded && (
        <div className="border-t border-tg-line bg-tg-bg px-4 py-3">
          <pre
            className="max-h-[40vh] overflow-auto text-[11px] leading-relaxed text-tg-ink"
            style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace' }}
          >
            {query.gsql}
          </pre>
          {query.expected_output_description && (
            <div className="mt-2 text-[11px] text-tg-mute">
              <span className="font-semibold">Expected output:</span>{' '}
              {query.expected_output_description}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
