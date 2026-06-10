'use client';

import clsx from 'clsx';
import {
  Check,
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
    if (!input.trim() || busy || !uploadedName) return;
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
        'flex h-full w-[560px] flex-col border-r border-tg-border bg-tg-panel',
        isDragActive && 'ring-2 ring-tg-purple ring-inset',
      )}
    >
      <input {...getInputProps()} />

      {/* Top bar */}
      <div className="flex items-center justify-between border-b border-tg-border px-5 py-3">
        <div>
          <h1 className="text-[15px] font-semibold text-tg-ink">Schema Assistant</h1>
          <div className="mt-0.5 flex items-center gap-1.5 text-[11.5px] text-tg-mute">
            <Sparkles size={11} className="text-tg-purple-500" />
            <span>Gemini · use case: {useCase.toLowerCase()}</span>
          </div>
        </div>
        <div className="flex items-center gap-1.5 text-[11.5px] text-tg-mute">
          <span className="h-1.5 w-1.5 rounded-full bg-green-500" />
          <span>fraud-detection</span>
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
            {busy && messages.length === 0 && steps.length === 0 && (
              <div className="flex justify-start tg-fade-in">
                <div className="mr-2 mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-tg-purple-100">
                  <Sparkles size={13} className="animate-pulse text-tg-purple-500" />
                </div>
                <div className="rounded-2xl border border-tg-line bg-tg-card px-3.5 py-2 text-[12.5px] text-tg-mute">
                  Surveying your data…
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Bottom input */}
      <div className="border-t border-tg-border px-5 py-3">
        {chips.length > 0 && uploadedName && !busy && (
          <div className="mb-2 flex flex-wrap gap-2">
            {chips.map((c, i) => (
              <button
                key={i}
                type="button"
                onClick={() => onChipClick(c)}
                className="rounded-full border border-tg-line bg-tg-card px-3 py-1 text-[11.5px] text-tg-ink transition-all hover:scale-[1.02] hover:border-tg-purple hover:bg-tg-hover hover:text-tg-purple-700"
              >
                {c}
              </button>
            ))}
          </div>
        )}

        <div className="flex items-center gap-2 rounded-xl border border-tg-line bg-tg-card px-3 py-2 shadow-card focus-within:border-tg-purple focus-within:ring-1 focus-within:ring-tg-purple-100">
          <input
            type="text"
            placeholder={
              !uploadedName
                ? 'Upload a CSV to start…'
                : busy
                  ? 'Agent is working…'
                  : 'Reply to the agent…'
            }
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={busy || !uploadedName}
            className="flex-1 bg-transparent text-[13px] text-tg-ink outline-none placeholder:text-tg-subtle disabled:cursor-not-allowed"
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit();
            }}
          />
          <button
            type="button"
            className="rounded-md bg-tg-purple p-1.5 text-white transition-colors hover:bg-tg-purple-600 disabled:bg-tg-line disabled:text-tg-subtle"
            disabled={!input.trim() || busy || !uploadedName}
            onClick={submit}
          >
            <Send size={13} />
          </button>
        </div>
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
      <div className="mr-2 mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-tg-purple-100">
        <Sparkles
          size={13}
          className={clsx('text-tg-purple-500', busy && 'animate-pulse')}
        />
      </div>
      <div className="flex-1 min-w-0 rounded-2xl border border-tg-line bg-tg-card px-3.5 py-2.5">
        <div className="mb-2 flex items-center gap-2 text-[10.5px] uppercase tracking-wide text-tg-mute">
          <Wrench size={11} className="text-tg-purple-500" />
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
                className="border-l-2 border-tg-purple/40 pl-2 text-[11.5px] italic text-tg-mute"
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
    ? <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-tg-purple-500 border-t-transparent" />
    : step.status === 'ok'
      ? <Check size={11} className="text-green-400" />
      : <X size={11} className="text-red-400" />;

  return (
    <div className="flex items-start gap-2 text-[11.5px]">
      <span className="mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center">
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <span className="font-medium text-tg-ink">{step.name}</span>
        <span className="text-tg-mute">{summarizeArgs(step.name, step.args)}</span>
        {step.summary && (
          <div className="mt-0.5 truncate text-[11px] text-tg-mute" title={step.summary}>
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
      <div className="mb-5 mt-3">
        <div className="mb-3 inline-flex items-center gap-1.5 rounded-full border border-tg-line bg-tg-card px-2.5 py-0.5 text-[10.5px] font-medium uppercase tracking-wide text-tg-purple-700">
          <Sparkles size={10} className="text-tg-purple-500" />
          Autograph · powered by Gemini
        </div>
        <h2 className="text-[22px] font-semibold leading-tight text-tg-ink">
          What decision are you trying to make?
        </h2>
        <p className="mt-1.5 text-[13px] leading-relaxed text-tg-mute">
          Tell me your goal and connect data. I&apos;ll investigate it,
          recommend a graph shape, and explain why.
        </p>
      </div>

      {/* Data source picker */}
      <div className="mb-5">
        <div className="mb-2 text-[10.5px] font-semibold uppercase tracking-wide text-tg-subtle">
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
        <div className="mb-2 text-[10.5px] font-semibold uppercase tracking-wide text-tg-subtle">
          Or pick a starting point
        </div>
        <UseCaseGrid selected={useCase} onSelect={onUseCaseChange} />
        <p className="mt-2 text-[11px] text-tg-mute">
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
        <div className="mr-2 mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-tg-purple-100">
          <Sparkles size={13} className="text-tg-purple-500" />
        </div>
      )}
      <div
        className={clsx(
          'max-w-[80%] rounded-2xl px-3.5 py-2 text-[12.5px] leading-relaxed',
          isUser
            ? 'bg-tg-purple text-white'
            : 'border border-tg-line bg-tg-card text-tg-ink',
        )}
      >
        {!isUser && isSchema && (
          <div className="mb-1.5 inline-flex items-center gap-1 rounded-full bg-tg-purple-100 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-tg-purple-700">
            <Sparkles size={9} />
            {message.type === 'update_schema' ? 'Schema updated' : 'Schema proposed'}
          </div>
        )}
        <div className="whitespace-pre-wrap">{message.content}</div>
        {isSchema && message.schema_json && (
          <div className="mt-2 border-t border-tg-line pt-2 text-[11px] text-tg-mute">
            {message.schema_json.vertices?.length ?? 0} vertices ·{' '}
            {message.schema_json.edges?.length ?? 0} edges — see the canvas →
          </div>
        )}
      </div>
    </div>
  );
}
