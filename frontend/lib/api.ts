import type {
  AgentErrorEvent,
  AgentFinalEvent,
  ChatMessage,
  ChatTurnResponse,
  DeployCountEvent,
  DeployDoneEvent,
  DeployPreview,
  DeployStepEvent,
  InstallStarterQueryResponse,
  PlanEvent,
  RunResponse,
  SchemaUpdateEvent,
  StarterQueriesResponse,
  ThinkingEvent,
  ToolCallEvent,
  ToolResultEvent,
  UseCase,
  UseCaseInfo,
} from './types';

// Default: call FastAPI directly. The Next.js rewrite proxy adds a Node
// HTTP-client timeout that kills long Gemini calls (>20s) with ECONNRESET.
// CORS on the backend is wide-open via TG_SCHEMA_CORS_ORIGINS, so direct
// calls work fine. Override with NEXT_PUBLIC_API_BASE if you need to proxy.
const BASE = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000/api';

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${res.status}: ${text}`);
  }
  return (await res.json()) as T;
}

export const api = {
  async health() {
    return jsonFetch<{ status: string; version: string; use_cases: string[] }>('/health');
  },

  async listUseCases() {
    return jsonFetch<UseCaseInfo[]>('/use-cases');
  },

  async createWorkspace() {
    return jsonFetch<{ workspace_id: string }>('/workspaces', { method: 'POST', body: '{}' });
  },

  async uploadFiles(workspaceId: string, files: File[]) {
    const form = new FormData();
    for (const f of files) form.append('files', f);
    const res = await fetch(`${BASE}/workspaces/${workspaceId}/files`, {
      method: 'POST',
      body: form,
    });
    if (!res.ok) throw new Error(`Upload failed: ${await res.text()}`);
    return (await res.json()) as { workspace_id: string; files: { name: string; bytes: number }[] };
  },

  async run(workspaceId: string, useCase: UseCase, opts?: { userPrompt?: string | null; useAi?: boolean }) {
    const body: Record<string, unknown> = { use_case: useCase, use_ai: opts?.useAi ?? true };
    if (opts?.userPrompt) body.user_prompt = opts.userPrompt;
    return jsonFetch<RunResponse>(`/workspaces/${workspaceId}/run`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },

  /** Open a Server-Sent Events stream for progress. */
  runStream(workspaceId: string, useCase: UseCase, onEvent: (name: string, data: unknown) => void) {
    // EventSource needs an absolute URL when crossing origins.
    const url = `${BASE}/workspaces/${workspaceId}/run/stream?use_case=${encodeURIComponent(useCase)}`;
    const es = new EventSource(url);

    const steps = ['step', 'done', 'error'];
    for (const s of steps) {
      es.addEventListener(s, (ev: MessageEvent) => {
        try {
          onEvent(s, JSON.parse(ev.data));
        } catch {
          onEvent(s, ev.data);
        }
      });
    }
    es.onerror = () => es.close();
    return es;
  },

  fileDownloadUrl(workspaceId: string, name: string) {
    return `${BASE}/workspaces/${workspaceId}/files/${encodeURIComponent(name)}`;
  },

  // ---- Conversational chat agent ----

  async chatHistory(workspaceId: string): Promise<ChatMessage[]> {
    return jsonFetch<ChatMessage[]>(`/workspaces/${workspaceId}/chat`);
  },

  async chatTurn(workspaceId: string, message: string, useCase: UseCase) {
    return jsonFetch<ChatTurnResponse>(`/workspaces/${workspaceId}/chat`, {
      method: 'POST',
      body: JSON.stringify({ message, use_case: useCase }),
    });
  },

  async chatClear(workspaceId: string): Promise<void> {
    await fetch(`${BASE}/workspaces/${workspaceId}/chat`, { method: 'DELETE' });
  },

  // ---- Deploy ----

  /** Dry-run: returns the deploy plan as text without touching TigerGraph. */
  async previewDeploy(workspaceId: string, csvFilename: string): Promise<DeployPreview> {
    return jsonFetch<DeployPreview>(`/workspaces/${workspaceId}/deploy`, {
      method: 'POST',
      body: JSON.stringify({
        csv_filename: csvFilename,
        creds: {},
        dry_run: true,
      }),
    });
  },

  /** Append a non-LLM event into the workspace's chat transcript. */
  async appendChatEvent(
    workspaceId: string,
    content: string,
    opts: { role?: 'agent' | 'system'; type?: 'answer' | 'progress' } = {},
  ): Promise<void> {
    await jsonFetch(`/workspaces/${workspaceId}/chat/event`, {
      method: 'POST',
      body: JSON.stringify({
        role: opts.role ?? 'agent',
        type: opts.type ?? 'progress',
        content,
      }),
    });
  },

  /** Generate LLM-authored starter GSQL queries for the deployed graph. */
  async generateStarterQueries(
    workspaceId: string,
    csvFilename: string,
  ): Promise<StarterQueriesResponse> {
    return jsonFetch<StarterQueriesResponse>(`/workspaces/${workspaceId}/queries/generate`, {
      method: 'POST',
      body: JSON.stringify({ csv_filename: csvFilename, creds: {}, dry_run: false }),
    });
  },

  /** Install a single starter query into TigerGraph. */
  async installStarterQuery(
    workspaceId: string,
    queryName: string,
    gsql: string,
  ): Promise<InstallStarterQueryResponse> {
    return jsonFetch<InstallStarterQueryResponse>(
      `/workspaces/${workspaceId}/queries/install`,
      {
        method: 'POST',
        body: JSON.stringify({ creds: {}, query_name: queryName, gsql }),
      },
    );
  },

  /** Stream the live deploy. Handlers are invoked as events arrive. */
  deployStream(
    workspaceId: string,
    csvFilename: string,
    handlers: {
      onStep?: (e: DeployStepEvent) => void;
      onCount?: (e: DeployCountEvent) => void;
      onDone?: (e: DeployDoneEvent) => void;
      onError?: (msg: string, code?: string) => void;
    },
    opts: { loadData?: boolean } = {},
  ): () => void {
    const abort = new AbortController();

    (async () => {
      try {
        const res = await fetch(`${BASE}/workspaces/${workspaceId}/deploy/stream`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
          body: JSON.stringify({
            csv_filename: csvFilename,
            creds: {},
            dry_run: false,
            load_data: !!opts.loadData,
          }),
          signal: abort.signal,
        });
        if (!res.ok || !res.body) {
          handlers.onError?.(`HTTP ${res.status}`);
          return;
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parts = buffer.split('\n\n');
          buffer = parts.pop() ?? '';
          for (const part of parts) {
            const lines = part.split('\n');
            let event = 'message';
            let dataStr = '';
            for (const line of lines) {
              if (line.startsWith('event: ')) event = line.slice(7).trim();
              else if (line.startsWith('data: ')) dataStr += line.slice(6);
            }
            if (!dataStr) continue;
            let data: Record<string, unknown>;
            try {
              data = JSON.parse(dataStr);
            } catch {
              continue;
            }
            switch (event) {
              case 'step':
                handlers.onStep?.(data as unknown as DeployStepEvent);
                break;
              case 'count':
                handlers.onCount?.(data as unknown as DeployCountEvent);
                break;
              case 'done':
                handlers.onDone?.(data as unknown as DeployDoneEvent);
                break;
              case 'error':
                handlers.onError?.(
                  String(data.message ?? 'unknown error'),
                  data.code as string | undefined,
                );
                break;
            }
          }
        }
      } catch (e: unknown) {
        if ((e as { name?: string }).name !== 'AbortError') {
          handlers.onError?.(e instanceof Error ? e.message : String(e));
        }
      }
    })();

    return () => abort.abort();
  },

  /**
   * Streaming variant of chatTurn. Calls `onDelta(partialMessage)` as each token
   * arrives so the UI can render Gemini's reply word-by-word. Calls `onFinal`
   * once the final structured payload (schema, score, …) is ready.
   *
   * Returns a cancel function.
   */
  /**
   * Run one agentic turn. Streams plan / thinking / tool_call / tool_result /
   * schema_update events while the agent is working, then a final event.
   */
  chatTurnStream(
    workspaceId: string,
    message: string,
    useCase: UseCase,
    handlers: {
      onPlan?: (e: PlanEvent) => void;
      onThinking?: (e: ThinkingEvent) => void;
      onToolCall?: (e: ToolCallEvent) => void;
      onToolResult?: (e: ToolResultEvent) => void;
      onSchemaUpdate?: (e: SchemaUpdateEvent) => void;
      onFinal?: (e: AgentFinalEvent) => void;
      onError?: (msg: string, code?: string) => void;
    },
  ): () => void {
    const abort = new AbortController();

    (async () => {
      try {
        const res = await fetch(`${BASE}/workspaces/${workspaceId}/chat/stream`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
          body: JSON.stringify({ message, use_case: useCase }),
          signal: abort.signal,
        });
        if (!res.ok || !res.body) {
          handlers.onError?.(`HTTP ${res.status}`);
          return;
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const parts = buffer.split('\n\n');
          buffer = parts.pop() ?? '';
          for (const part of parts) {
            const lines = part.split('\n');
            let event = 'message';
            let dataStr = '';
            for (const line of lines) {
              if (line.startsWith('event: ')) event = line.slice(7).trim();
              else if (line.startsWith('data: ')) dataStr += line.slice(6);
            }
            if (!dataStr) continue;
            let data: Record<string, unknown>;
            try {
              data = JSON.parse(dataStr);
            } catch {
              continue;
            }
            switch (event) {
              case 'plan':
                handlers.onPlan?.(data as unknown as PlanEvent);
                break;
              case 'thinking':
                handlers.onThinking?.(data as unknown as ThinkingEvent);
                break;
              case 'tool_call':
                handlers.onToolCall?.(data as unknown as ToolCallEvent);
                break;
              case 'tool_result':
                handlers.onToolResult?.(data as unknown as ToolResultEvent);
                break;
              case 'schema_update':
                handlers.onSchemaUpdate?.(data as unknown as SchemaUpdateEvent);
                break;
              case 'final':
                handlers.onFinal?.(data as unknown as AgentFinalEvent);
                break;
              case 'error':
                handlers.onError?.(
                  String(data.message ?? 'unknown error'),
                  data.code as string | undefined,
                );
                break;
            }
          }
        }
      } catch (e: unknown) {
        if ((e as { name?: string }).name !== 'AbortError') {
          handlers.onError?.(e instanceof Error ? e.message : String(e));
        }
      }
    })();

    return () => abort.abort();
  },
};

