'use client';

import clsx from 'clsx';
import {
  Check,
  Paperclip,
  Send,
  Sparkles,
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
  messages: ChatMessage[];
  steps: AgentStep[];
  onSend: (message: string) => Promise<void> | void;
  busy: boolean;
  useCase: UseCase;
  onUseCaseChange: (uc: UseCase) => void;
  hasWorkspace: boolean;
}

export default function ChatPanel({
  uploadedName,
  onFilesPicked,
  messages,
  steps,
  onSend,
  busy,
  useCase,
  onUseCaseChange,
  hasWorkspace,
}: Props) {
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, steps, busy]);

  const submit = () => {
    if (!input.trim() || busy || !hasWorkspace) return;
    void onSend(input.trim());
    setInput('');
  };

  const onChipClick = (reply: string) => {
    if (busy) return;
    void onSend(reply);
  };

  const onDrop = useCallback(
    (accepted: File[]) => {
      if (accepted.length) void onFilesPicked(accepted);
    },
    [onFilesPicked],
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

  return (
    <div
      {...getRootProps()}
      className={clsx(
        'flex h-full w-[560px] flex-col border-r border-tgl-border bg-tgl-panel',
        isDragActive && 'ring-2 ring-tg-orange ring-inset',
      )}
    >
      <input {...getInputProps()} />

      {/* Top bar — Savanna AI + AGENT ACTIVE pill */}
      <div className="flex items-center justify-between border-b border-tgl-border px-5 py-3">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-tgl-bubble">
            <Sparkles size={14} className="text-tg-orange" />
          </div>
          <h1 className="text-[15px] font-semibold text-tgl-ink">Savanna AI</h1>
        </div>
        <div className="flex items-center gap-1.5 rounded-full bg-tgl-activeBg px-2.5 py-1 text-[10.5px] font-semibold uppercase tracking-wide text-tgl-activeInk">
          <span className="h-1.5 w-1.5 rounded-full bg-tgl-activeDot" />
          <span>Agent Active</span>
        </div>
      </div>

      {/* Conversation area */}
      <div className="flex-1 overflow-y-auto px-5 py-5">
        {!uploadedName ? (
          <WelcomeScreen
            isDragActive={isDragActive}
            disabled={!hasWorkspace}
            onFilesPicked={onFilesPicked}
            uploadedName={uploadedName}
            useCase={useCase}
            onUseCaseChange={onUseCaseChange}
          />
        ) : (
          <div className="space-y-4">
            {messages.map((m, i) => (
              <MessageBubble key={i} message={m} />
            ))}
            {/* Agent's live work log for the current turn */}
            {steps.length > 0 && <AgentStepsBlock steps={steps} busy={busy} />}
            {/* Persistent "Thinking..." bubble — shows whenever the agent
                is mid-turn so the user knows it's working, like Claude Code */}
            {busy && <ThinkingBubble hasSteps={steps.length > 0} />}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Bottom input */}
      <div className="border-t border-tgl-border bg-tgl-panel px-5 py-3">
        {chips.length > 0 && uploadedName && !busy && (
          <div className="mb-2.5 flex flex-wrap gap-2">
            {chips.map((c, i) => (
              <button
                key={i}
                type="button"
                onClick={() => onChipClick(c)}
                className="rounded-xl bg-tgl-chip px-3.5 py-2 text-[12.5px] font-medium text-tgl-chipInk transition-colors hover:bg-tgl-chipHover"
              >
                {c}
              </button>
            ))}
          </div>
        )}

        <div className="flex items-center gap-2 rounded-xl border border-tgl-border bg-tgl-card px-3 py-2 focus-within:border-tg-orange focus-within:ring-1 focus-within:ring-tg-orange/30">
          {/* Upload-in-chat button — opens the file picker mid-conversation */}
          <button
            type="button"
            onClick={() => open()}
            disabled={busy || !hasWorkspace}
            title="Upload a CSV"
            className="rounded-md p-1.5 text-tgl-mute transition-colors hover:bg-tgl-bubble hover:text-tg-orange disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Paperclip size={14} />
          </button>
          <input
            type="text"
            placeholder={
              busy
                ? 'Agent is working…'
                : !uploadedName
                  ? 'Ask Savanna anything about TigerGraph…'
                  : 'Reply to Savanna…'
            }
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={busy || !hasWorkspace}
            className="flex-1 bg-transparent text-[13px] text-tgl-ink outline-none placeholder:text-tgl-subtle disabled:cursor-not-allowed"
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit();
            }}
          />
          <button
            type="button"
            className="rounded-md bg-tg-orange p-1.5 text-white transition-colors hover:opacity-90 disabled:bg-tgl-line disabled:text-tgl-subtle"
            disabled={!input.trim() || busy || !hasWorkspace}
            onClick={submit}
          >
            <Send size={13} />
          </button>
        </div>
      </div>
    </div>
  );
}

// -------------------- Thinking indicator --------------------
// Shows whenever the agent is mid-turn, like Claude Code's "Thinking..." dot.
// When tools are running it sits under the AgentStepsBlock so the user always
// knows there's progress happening.

function ThinkingBubble({ hasSteps }: { hasSteps: boolean }) {
  return (
    <div className="flex tg-fade-in">
      {!hasSteps && (
        <div className="mr-2 mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-tgl-bubble">
          <Sparkles size={13} className="animate-pulse text-tg-orange" />
        </div>
      )}
      <div
        className={clsx(
          'flex items-center gap-2 rounded-2xl bg-tgl-bubble px-4 py-2.5 text-[12.5px] text-tgl-mute',
          hasSteps && 'ml-9',
        )}
      >
        <span className="font-medium text-tgl-ink">Thinking</span>
        <span className="flex gap-0.5">
          <span className="h-1 w-1 animate-bounce rounded-full bg-tg-orange [animation-delay:-0.3s]" />
          <span className="h-1 w-1 animate-bounce rounded-full bg-tg-orange [animation-delay:-0.15s]" />
          <span className="h-1 w-1 animate-bounce rounded-full bg-tg-orange" />
        </span>
      </div>
    </div>
  );
}

// -------------------- Agent steps (tool calls + thinking) --------------------

function AgentStepsBlock({ steps, busy }: { steps: AgentStep[]; busy: boolean }) {
  const toolCallCount = steps.filter((s) => s.kind === 'tool_call').length;
  const okCount = steps.filter(
    (s) => s.kind === 'tool_call' && s.status === 'ok',
  ).length;

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
            {okCount}/{toolCallCount} steps
            {busy && <span className="animate-pulse"> …</span>}
          </span>
        </div>
        <div className="space-y-1">
          {steps.map((s, i) =>
            s.kind === 'thinking' ? (
              <div
                key={i}
                className="border-l-2 border-tg-orange/40 pl-2 text-[11.5px] italic text-tgl-mute"
              >
                {s.text}
              </div>
            ) : (
              <ToolCallLine key={i} step={s} />
            ),
          )}
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
  useCase: UseCase;
  onUseCaseChange: (uc: UseCase) => void;
}) {
  const [pickedSource, setPickedSource] = useState<string>('upload');

  return (
    <div className={clsx('flex h-full flex-col', isDragActive && 'opacity-90')}>
      {/* Conversational welcome — matches TG Cloud "Savanna AI" intro */}
      <div className="mb-5 flex tg-fade-in">
        <div className="mr-2 mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-tgl-bubble">
          <Sparkles size={13} className="text-tg-orange" />
        </div>
        <div className="max-w-[80%] rounded-2xl bg-tgl-bubble px-4 py-2.5 text-[13px] leading-relaxed text-tgl-ink">
          <div>Hi! I&apos;m Savanna AI.</div>
          <div className="mt-1">
            I can help you design a graph schema based on the business problem
            you&apos;re trying to solve.
          </div>
          <div className="mt-1">What would you like to accomplish today?</div>
        </div>
      </div>

      {/* Data source picker */}
      <div className="mb-5">
        <div className="mb-2 text-[10.5px] font-semibold uppercase tracking-wide text-tgl-subtle">
          Where&apos;s your data?
        </div>
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
          Or pick a starting point
        </div>
        <UseCaseGrid selected={useCase} onSelect={onUseCaseChange} />
        <p className="mt-2 text-[11px] text-tgl-mute">
          The agent will still ask about your specific decision — this just
          biases which industry patterns it considers first.
        </p>
      </div>
    </div>
  );
}

// -------------------- Chat bubbles --------------------

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';
  const isSchema =
    message.type === 'propose_schema' || message.type === 'update_schema';

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
            : 'bg-tgl-bubble text-tgl-ink',
        )}
      >
        {!isUser && isSchema && (
          <div className="mb-1.5 inline-flex items-center gap-1 rounded-full bg-tgl-chip px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-tgl-chipInk">
            <Sparkles size={9} />
            {message.type === 'update_schema' ? 'Schema updated' : 'Schema proposed'}
          </div>
        )}
        <div className="whitespace-pre-wrap">{message.content}</div>
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
