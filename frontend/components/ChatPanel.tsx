'use client';

import clsx from 'clsx';
import {
  Check,
  Paperclip,
  Send,
  Sparkles,
  Square,
  Wrench,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useDropzone } from 'react-dropzone';
import type { ChatMessage, UseCase } from '@/lib/types';
import DataSourceGrid from './DataSourceGrid';
import UseCaseGrid from './UseCaseGrid';

// ---------- Agent step types (visible in the chat panel while the agent works) ----------

export type AgentStep =
  | { kind: 'thinking'; text: string }
  | {
      kind: 'tool_call';
      id: string;
      name: string;
      args: Record<string, unknown>;
      status: 'running' | 'ok' | 'failed';
      summary?: string;
    };

interface Props {
  uploadedName: string | null;
  onFilesPicked: (files: File[]) => Promise<void> | void;
  /** Attach a file mid-chat WITHOUT auto-sending a turn (paperclip / drag). */
  onAttachFile: (files: File[]) => Promise<void> | void;
  messages: ChatMessage[];
  steps: AgentStep[];
  onSend: (message: string) => Promise<void> | void;
  /** Abort the in-flight turn (Stop button). */
  onStop: () => void;
  busy: boolean;
  useCase: UseCase | null;
  onUseCaseChange: (uc: UseCase) => void;
  hasWorkspace: boolean;
}

export default function ChatPanel({
  uploadedName,
  onFilesPicked,
  onAttachFile,
  messages,
  steps,
  onSend,
  onStop,
  busy,
  useCase,
  onUseCaseChange,
  hasWorkspace,
}: Props) {
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  // Auto-grow the textarea up to a max height, then scroll.
  const autoGrow = useCallback(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 140) + 'px';
  }, []);

  useEffect(() => {
    autoGrow();
  }, [input, autoGrow]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, steps, busy]);

  const submit = () => {
    if (!input.trim() || busy || !hasWorkspace) return;
    void onSend(input.trim());
    setInput('');
  };

  // Insert a newline at the cursor (Shift+Enter handles itself; this powers
  // Ctrl/Cmd+Enter which the browser otherwise ignores in a textarea).
  const insertNewline = () => {
    const el = taRef.current;
    if (!el) {
      setInput((v) => v + '\n');
      return;
    }
    const s = el.selectionStart ?? input.length;
    const e2 = el.selectionEnd ?? input.length;
    setInput(input.slice(0, s) + '\n' + input.slice(e2));
    requestAnimationFrame(() => {
      el.selectionStart = el.selectionEnd = s + 1;
      autoGrow();
    });
  };

  const onChipClick = (reply: string) => {
    if (busy) return;
    void onSend(reply);
  };

  // Drag-drop and the paperclip ATTACH the file without auto-sending — the
  // user types/sends when ready (onFilesPicked w/ kickoff is only the welcome
  // "upload CSV" entry point).
  const onDrop = useCallback(
    (accepted: File[]) => {
      if (accepted.length) void onAttachFile(accepted);
    },
    [onAttachFile],
  );
  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    onDrop,
    multiple: true,
    accept: { 'text/csv': ['.csv'], 'application/octet-stream': ['.csv'] },
    noClick: true,
    noKeyboard: true,
  });

  const lastAgent = [...messages].reverse().find((m) => m.role === 'agent');
  const chips = lastAgent?.suggested_replies ?? [];

  // The agent's latest in-flight thought (for the compact Claude-Code-style
  // indicator) and whether any tools are running this turn.
  const latestThought = [...steps]
    .reverse()
    .find((s): s is Extract<AgentStep, { kind: 'thinking' }> => s.kind === 'thinking')
    ?.text;
  const hasToolCalls = steps.some((s) => s.kind === 'tool_call');

  // Pill stays green ("Agent Active") and just animates the dot when busy —
  // an orange/peach swap looked alarming to users (read as "warning / error").
  const pillClass = 'bg-tgl-activeBg text-tgl-activeInk';
  const pillDotClass = clsx('bg-tgl-activeDot', busy && 'animate-pulse');

  return (
    <div
      {...getRootProps()}
      className={clsx(
        'flex h-full w-[360px] shrink-0 flex-col border-r border-tgl-border bg-tgl-panel',
        isDragActive && 'ring-2 ring-tg-orange ring-inset',
      )}
    >
      <input {...getInputProps()} />

      {/* Top bar — Savanna AI + Agent Active pill */}
      <div className="flex items-center justify-between border-b border-tgl-border px-4 py-3">
        <div className="flex items-center gap-2">
          <Sparkles size={15} className="text-tg-orange" />
          <h1 className="text-[14px] font-semibold text-tgl-ink">Savanna AI</h1>
        </div>
        <div
          className={clsx(
            'flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide',
            pillClass,
          )}
        >
          <span className={clsx('h-1.5 w-1.5 rounded-full', pillDotClass)} />
          <span>Agent Active</span>
        </div>
      </div>

      {/* Conversation area */}
      <div className="flex-1 overflow-y-auto px-5 py-5">
        {!uploadedName ? (
          // As soon as the user uploads (busy flips true) show an immediate
          // "getting started" state instead of the welcome picker — no dead
          // gap while the CSV uploads + the agent spins up.
          busy ? (
            <KickoffProcessing />
          ) : (
            <WelcomeScreen
              isDragActive={isDragActive}
              disabled={!hasWorkspace}
              onFilesPicked={onFilesPicked}
              uploadedName={uploadedName}
              useCase={useCase}
              onUseCaseChange={onUseCaseChange}
            />
          )
        ) : (
          <div className="space-y-4">
            {messages.map((m, i) => (
              <MessageBubble
                key={i}
                message={m}
                // Stream only the newest agent message word-by-word.
                animate={i === messages.length - 1}
              />
            ))}
            {/* Compact tool-call progress (only while working, only if tools
                are actually running). Verbose thinking is NOT dumped here —
                it rides the single Thinking indicator below. */}
            {busy && hasToolCalls && <AgentStepsBlock steps={steps} busy={busy} />}
            {/* Single compact Thinking indicator — shows the latest thought
                (truncated) like Claude Code, and vanishes when the answer
                lands (steps are cleared on final). */}
            {busy && (
              <ThinkingBubble hasSteps={hasToolCalls} latestThought={latestThought} />
            )}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Bottom input */}
      <div className="border-t border-tgl-border bg-tgl-panel px-5 py-3">
        {chips.length > 0 && !busy && (() => {
          const isConfirm =
            lastAgent?.type === 'question' &&
            isDestructiveQuestion(lastAgent.content);
          return (
            <div className="mb-2.5 flex flex-wrap gap-2">
              {chips.map((c, i) => {
                // Destructive Yes/No: red affirmative + neutral cancel.
                const isAffirm = isConfirm && isAffirmativeChip(c);
                const isCancel = isConfirm && !isAffirm;
                return (
                  <button
                    key={i}
                    type="button"
                    onClick={() => onChipClick(c)}
                    className={clsx(
                      'rounded-xl px-3.5 py-2 text-[12.5px] font-medium transition-colors',
                      isAffirm
                        ? 'bg-red-50 text-red-700 hover:bg-red-100 border border-red-200'
                        : isCancel
                          ? 'bg-tgl-card text-tgl-ink border border-tgl-border hover:bg-tgl-bubble'
                          : 'bg-tgl-chip text-tgl-chipInk hover:bg-tgl-chipHover',
                    )}
                  >
                    {c}
                  </button>
                );
              })}
            </div>
          );
        })()}

        {/* Active / attached file chip — confirms the paperclip attached a
            file without firing a turn; the user sends when ready. */}
        {uploadedName && (
          <div className="mb-1.5 inline-flex max-w-full items-center gap-1.5 rounded-md bg-tgl-chip px-2 py-1 text-[11px] text-tgl-chipInk">
            <Paperclip size={11} className="shrink-0" />
            <span className="truncate">{uploadedName}</span>
          </div>
        )}

        <div className="flex items-end gap-2 rounded-lg border border-tgl-border bg-tgl-card px-3 py-2 focus-within:border-tg-orange/60">
          {/* Attach-in-chat — paperclip attaches a CSV WITHOUT sending */}
          <button
            type="button"
            onClick={() => open()}
            disabled={busy || !hasWorkspace}
            title="Attach a CSV (won't send)"
            className="mb-0.5 text-tgl-mute transition-colors hover:text-tg-orange disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Paperclip size={14} />
          </button>
          <textarea
            ref={taRef}
            rows={1}
            placeholder={
              busy ? 'Agent is working… (Stop to interrupt)' : 'Ask Savanna…  (Enter to send, Shift/Ctrl+Enter for newline)'
            }
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={!hasWorkspace}
            className="flex-1 resize-none bg-transparent py-0.5 text-[13px] leading-relaxed text-tgl-ink outline-none placeholder:text-tgl-subtle disabled:cursor-not-allowed"
            style={{ maxHeight: 140 }}
            onKeyDown={(e) => {
              if (e.key !== 'Enter') return;
              if (e.shiftKey) return; // browser inserts newline
              if (e.ctrlKey || e.metaKey) {
                e.preventDefault();
                insertNewline();
                return;
              }
              e.preventDefault();
              submit();
            }}
          />
          {busy ? (
            <button
              type="button"
              className="mb-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-red-50 text-red-600 transition-colors hover:bg-red-100"
              onClick={onStop}
              title="Stop generating"
            >
              <Square size={11} className="fill-current" />
            </button>
          ) : (
            <button
              type="button"
              className="mb-0.5 text-tg-orange transition-colors hover:opacity-80 disabled:text-tgl-subtle"
              disabled={!input.trim() || !hasWorkspace}
              onClick={submit}
              title="Send"
            >
              <Send size={14} />
            </button>
          )}
        </div>
        <p className="mt-1.5 text-center text-[10.5px] text-tgl-subtle">
          Savanna can help generate schema, GSQL, and more.
        </p>
      </div>
    </div>
  );
}

// -------------------- Question / confirmation helpers --------------------

/** Best-effort detection: does this question ask permission for a
 *  destructive operation? Used to escalate the visual treatment to a
 *  red confirmation card + red "Yes" chip. */
function isDestructiveQuestion(text: string): boolean {
  const t = (text || '').toLowerCase();
  return /\b(drop|delete|wipe|clear|remove|destroy|overwrite|reset)\b/.test(t);
}

/** A chip is the "affirmative" answer (the destructive Yes) when its
 *  text starts with yes / confirm / delete / drop / wipe / etc. */
function isAffirmativeChip(text: string): boolean {
  const t = (text || '').trim().toLowerCase();
  return /^(yes|confirm|delete|drop|wipe|clear|remove|destroy|proceed|do it|go ahead)/.test(t);
}

// -------------------- Thinking indicator --------------------
// Shows whenever the agent is mid-turn, like Claude Code's "Thinking..." dot.
// When tools are running it sits under the AgentStepsBlock so the user always
// knows there's progress happening.

function ThinkingBubble({
  hasSteps,
  latestThought,
}: {
  hasSteps: boolean;
  latestThought?: string;
}) {
  // Keep the live thought to a single compact line — never let the raw
  // reasoning grow and push the conversation up the screen.
  const thought = (latestThought || '').replace(/\s+/g, ' ').trim();
  const preview = thought.length > 90 ? thought.slice(0, 90) + '…' : thought;

  return (
    <div className="flex tg-fade-in">
      {!hasSteps && (
        <div className="mr-2 mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-tgl-bubble">
          <Sparkles size={13} className="animate-pulse text-tg-orange" />
        </div>
      )}
      <div
        className={clsx(
          'flex min-w-0 items-center gap-2 rounded-2xl bg-tgl-bubble px-4 py-2.5 text-[12.5px] text-tgl-mute',
          hasSteps && 'ml-9',
        )}
      >
        <span className="shrink-0 font-medium text-tgl-ink">Thinking</span>
        <span className="flex shrink-0 gap-0.5">
          <span className="h-1 w-1 animate-bounce rounded-full bg-tg-orange [animation-delay:-0.3s]" />
          <span className="h-1 w-1 animate-bounce rounded-full bg-tg-orange [animation-delay:-0.15s]" />
          <span className="h-1 w-1 animate-bounce rounded-full bg-tg-orange" />
        </span>
        {preview && (
          <span className="truncate italic text-tgl-subtle">{preview}</span>
        )}
      </div>
    </div>
  );
}

// -------------------- Kickoff processing (immediate upload feedback) --------------------
// Shown the instant a CSV upload starts, so there's no blank gap before the
// agent's first thinking event arrives.

function KickoffProcessing() {
  return (
    <div className="flex h-full flex-col items-center justify-center text-center tg-fade-in">
      <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-tgl-bubble">
        <Sparkles size={20} className="animate-pulse text-tg-orange" />
      </div>
      <div className="flex items-center gap-2 text-[13px] font-medium text-tgl-ink">
        <span>Analyzing your data</span>
        <span className="flex gap-0.5">
          <span className="h-1 w-1 animate-bounce rounded-full bg-tg-orange [animation-delay:-0.3s]" />
          <span className="h-1 w-1 animate-bounce rounded-full bg-tg-orange [animation-delay:-0.15s]" />
          <span className="h-1 w-1 animate-bounce rounded-full bg-tg-orange" />
        </span>
      </div>
      <p className="mt-1.5 text-[11.5px] text-tgl-mute">
        Uploading your CSV and starting the agent…
      </p>
    </div>
  );
}

// -------------------- Agent steps (tool calls + thinking) --------------------

function AgentStepsBlock({ steps, busy }: { steps: AgentStep[]; busy: boolean }) {
  // Only the tool calls render here — the model's raw reasoning ("thinking")
  // is intentionally NOT dumped (it grew unboundedly and pushed the chat up).
  // To keep the list compact we also cap it to the most recent calls.
  const toolCalls = steps.filter(
    (s): s is Extract<AgentStep, { kind: 'tool_call' }> => s.kind === 'tool_call',
  );
  const okCount = toolCalls.filter((s) => s.status === 'ok').length;
  const MAX_VISIBLE = 6;
  const hidden = Math.max(0, toolCalls.length - MAX_VISIBLE);
  const visible = toolCalls.slice(-MAX_VISIBLE);

  return (
    <div className="flex tg-fade-in">
      <div className="mr-2 mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-tgl-bubble">
        <Sparkles
          size={13}
          className={clsx('text-tg-orange', busy && 'animate-pulse')}
        />
      </div>
      <div className="flex-1 min-w-0 rounded-2xl bg-tgl-bubble px-4 py-3">
        <div className="mb-2 flex items-center gap-2 text-[10.5px] uppercase tracking-wide text-tgl-mute">
          <Wrench size={11} className="text-tg-orange" />
          <span className="font-semibold">Agent at work</span>
          <span>·</span>
          <span>
            {okCount}/{toolCalls.length} steps
            {busy && <span className="animate-pulse"> …</span>}
          </span>
        </div>
        <div className="space-y-1">
          {hidden > 0 && (
            <div className="pl-1 text-[11px] text-tgl-subtle">+{hidden} earlier steps…</div>
          )}
          {visible.map((s) => (
            <ToolCallLine key={s.id} step={s} />
          ))}
        </div>
      </div>
    </div>
  );
}

function ToolCallLine({
  step,
}: {
  step: Extract<AgentStep, { kind: 'tool_call' }>;
}) {
  const icon = step.status === 'running'
    ? <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-tg-orange border-t-transparent" />
    : step.status === 'ok'
      ? <Check size={11} className="text-tgl-activeInk" />
      : <X size={11} className="text-red-500" />;

  return (
    <div className="flex items-start gap-2 text-[11.5px]">
      <span className="mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center">
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <span className="font-medium text-tgl-ink">{step.name}</span>
        <span className="text-tgl-mute">{summarizeArgs(step.name, step.args)}</span>
        {step.summary && (
          <div className="mt-0.5 truncate text-[11px] text-tgl-mute" title={step.summary}>
            → {step.summary}
          </div>
        )}
      </div>
    </div>
  );
}

function summarizeArgs(name: string, args: Record<string, unknown>): string {
  // Pick out a couple of the most useful args for inline display
  switch (name) {
    case 'inspect_column':
      return `(${args.table}.${args.column})`;
    case 'find_columns_matching':
      return `(/${args.pattern}/)`;
    case 'get_sample_rows':
      return `(${args.table}, n=${args.n ?? 3})`;
    case 'propose_vertex':
    case 'remove_vertex':
      return `(${args.name})`;
    case 'propose_edge':
    case 'remove_edge':
      return `(${args.name})`;
    case 'finalize_schema':
      return '';
    case 'ask_user':
      return '';
    default:
      return '';
  }
}

// -------------------- Welcome screen --------------------

function WelcomeScreen({
  isDragActive,
  disabled,
  onFilesPicked,
  uploadedName,
  useCase,
  onUseCaseChange,
}: {
  isDragActive: boolean;
  disabled: boolean;
  onFilesPicked: (files: File[]) => Promise<void> | void;
  uploadedName: string | null;
  useCase: UseCase | null;
  onUseCaseChange: (uc: UseCase) => void;
}) {
  const [pickedSource, setPickedSource] = useState<string>('upload');

  return (
    <div className={clsx('flex h-full flex-col', isDragActive && 'opacity-90')}>
      {/* Intro line — matches TG Cloud Savanna AI welcome */}
      <p className="mb-3 text-[12.5px] leading-relaxed text-tgl-mute">
        Choose a use case and Savanna AI will suggest a graph schema for your data.
      </p>

      {/* Data source picker (vertical list) */}
      <div className="mb-4">
        <DataSourceGrid
          selected={pickedSource}
          onSelect={setPickedSource}
          onFilesPicked={(files) => {
            if (disabled) return;
            void onFilesPicked(files);
          }}
          uploadedName={uploadedName}
        />
      </div>

      {/* Use case picker — soft pattern hint */}
      <div className="mb-2">
        <div className="mb-2 text-[10.5px] font-semibold uppercase tracking-wide text-tgl-subtle">
          Build your graph schema
        </div>
        <UseCaseGrid selected={useCase} onSelect={onUseCaseChange} />
      </div>
    </div>
  );
}

// -------------------- Chat bubbles --------------------

// Reveals text word-by-word on mount (typewriter). Used for the newest
// agent message so the answer "streams" in like Claude Code, then settles.
function Typewriter({ text, animate }: { text: string; animate: boolean }) {
  const [shown, setShown] = useState(animate ? '' : text);

  useEffect(() => {
    if (!animate) {
      setShown(text);
      return;
    }
    // Split keeping whitespace tokens so re-joining reconstructs exactly.
    const tokens = text.split(/(\s+)/);
    let i = 0;
    setShown('');
    const id = setInterval(() => {
      i += 1;
      setShown(tokens.slice(0, i).join(''));
      if (i >= tokens.length) clearInterval(id);
    }, 22);
    return () => clearInterval(id);
  }, [text, animate]);

  return <div className="whitespace-pre-wrap">{shown}</div>;
}

function MessageBubble({
  message,
  animate = false,
}: {
  message: ChatMessage;
  animate?: boolean;
}) {
  const isUser = message.role === 'user';
  const isSchema =
    message.type === 'propose_schema' || message.type === 'update_schema';
  const isQuestion = !isUser && message.type === 'question';
  const isDestructive = isQuestion && isDestructiveQuestion(message.content);

  return (
    <div
      className={clsx('flex tg-fade-in', isUser ? 'justify-end' : 'justify-start')}
    >
      {!isUser && (
        <div className="mr-2 mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-tgl-bubble">
          <Sparkles size={13} className="text-tg-orange" />
        </div>
      )}
      <div
        className={clsx(
          'max-w-[80%] rounded-2xl px-4 py-2.5 text-[13px] leading-relaxed',
          isUser
            ? 'border border-tgl-border bg-tgl-card text-tgl-ink'
            : isQuestion
              ? clsx(
                  'border-2 bg-tgl-card text-tgl-ink shadow-sm',
                  isDestructive ? 'border-red-300' : 'border-tg-orange/40',
                )
              : 'bg-tgl-bubble text-tgl-ink',
        )}
      >
        {!isUser && isSchema && (
          <div className="mb-1.5 inline-flex items-center gap-1 rounded-full bg-tgl-chip px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-tgl-chipInk">
            <Sparkles size={9} />
            {message.type === 'update_schema' ? 'Schema updated' : 'Schema proposed'}
          </div>
        )}
        {isQuestion && (
          <div
            className={clsx(
              'mb-1.5 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide',
              isDestructive ? 'bg-red-50 text-red-700' : 'bg-tgl-chip text-tgl-chipInk',
            )}
          >
            {isDestructive ? 'Confirm — destructive' : 'Savanna asks'}
          </div>
        )}
        {isUser ? (
          <div className="whitespace-pre-wrap">{message.content}</div>
        ) : (
          // Stream the newest agent answer word-by-word; older ones render full.
          <Typewriter text={message.content} animate={animate} />
        )}
        {isSchema && message.schema_json && (
          <div className="mt-2 border-t border-tgl-line pt-2 text-[11px] text-tgl-mute">
            {message.schema_json.vertices?.length ?? 0} vertices ·{' '}
            {message.schema_json.edges?.length ?? 0} edges — see the canvas →
          </div>
        )}
      </div>
    </div>
  );
}
