'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type {
  ChatMessage,
  CriticReview,
  Schema,
  SchemaScore,
  UseCase,
  ValidationResult,
} from '@/lib/types';
import ChatPanel, { type AgentStep } from '@/components/ChatPanel';
import DeployModal, { type DeployModalMode } from '@/components/DeployModal';
import SchemaPreview from '@/components/SchemaPreview';
import Sidebar from '@/components/Sidebar';
import StarterQueriesPanel from '@/components/StarterQueriesPanel';

export default function Page() {
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  // Use case is a soft hint that drives the backend pattern library; the
  // agent still asks the user about their decision in Stage 1. Default
  // FRAUD because that's the only fully-fleshed pattern today.
  const [useCase, setUseCase] = useState<UseCase>('FRAUD');
  const [uploadedName, setUploadedName] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [busy, setBusy] = useState(false);
  const [schema, setSchema] = useState<Schema | null>(null);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [score, setScore] = useState<SchemaScore | null>(null);
  const [critic, _setCritic] = useState<CriticReview | null>(null);
  const [confidence, setConfidence] = useState<'High' | 'Medium' | 'Low' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deployModal, setDeployModal] = useState<DeployModalMode | null>(null);
  const [starterQueriesOpen, setStarterQueriesOpen] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const ws = await api.createWorkspace();
        setWorkspaceId(ws.workspace_id);
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, []);

  const sendChat = useCallback(
    async (message: string) => {
      if (!workspaceId) return;
      setError(null);
      setBusy(true);
      setSteps([]); // reset the live step log at the start of every turn

      // Optimistically append user message
      if (message.trim()) {
        setMessages((prev) => [
          ...prev,
          { role: 'user', content: message, type: 'answer' },
        ]);
      }

      return new Promise<void>((resolve) => {
        api.chatTurnStream(workspaceId, message, useCase, {
          onThinking: (e) => {
            setSteps((prev) => [...prev, { kind: 'thinking', text: e.text }]);
          },
          onToolCall: (e) => {
            setSteps((prev) => [
              ...prev,
              {
                kind: 'tool_call',
                id: e.id,
                name: e.name,
                args: e.args,
                status: 'running',
              },
            ]);
          },
          onToolResult: (e) => {
            setSteps((prev) =>
              prev.map((s) =>
                s.kind === 'tool_call' && s.id === e.id
                  ? { ...s, status: e.ok ? 'ok' : 'failed', summary: e.summary }
                  : s,
              ),
            );
          },
          onSchemaUpdate: (e) => {
            // Progressive canvas update — render every new vertex/edge as it lands
            setSchema(e.schema);
          },
          onFinal: (payload) => {
            // Append the finalized agent message
            setMessages((prev) => [
              ...prev,
              {
                role: 'agent',
                content: payload.message,
                type: payload.type as ChatMessage['type'],
                suggested_replies: payload.suggested_replies,
                schema_json: payload.schema ?? undefined,
              },
            ]);
            if (payload.schema) setSchema(payload.schema);
            if (payload.validation) setValidation(payload.validation);
            if (payload.score) setScore(payload.score);
            if (payload.confidence) setConfidence(payload.confidence);
            setBusy(false);
            // Keep the step log visible after the turn finishes so the user
            // can see what the agent did. We'll reset on next turn.
            resolve();
          },
          onError: (msg) => {
            setError(msg);
            if (message.trim()) setMessages((prev) => prev.slice(0, -1));
            setBusy(false);
            resolve();
          },
        });
      });
    },
    [workspaceId, useCase],
  );

  const handleFilesPicked = useCallback(
    async (files: File[]) => {
      if (!workspaceId) return;
      setError(null);
      setBusy(true);
      try {
        const res = await api.uploadFiles(workspaceId, files);
        const lastName = res.files[res.files.length - 1]?.name ?? null;
        setUploadedName(lastName);
        // Kickoff the agent right after upload
        await sendChat('');
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : String(e));
        setBusy(false);
      }
    },
    [workspaceId, sendChat],
  );

  return (
    <div className="flex h-screen overflow-hidden bg-tg-bg">
      <Sidebar />
      <main className="flex flex-1 overflow-hidden">
        <ChatPanel
          uploadedName={uploadedName}
          onFilesPicked={handleFilesPicked}
          messages={messages}
          steps={steps}
          onSend={sendChat}
          busy={busy}
          useCase={useCase}
          onUseCaseChange={setUseCase}
          hasWorkspace={!!workspaceId}
        />
        <SchemaPreview
          schema={schema}
          validation={validation}
          score={score}
          critic={critic}
          confidence={confidence}
          workspaceLabel="fraud-detection"
          onGenerate={() => sendChat('just design it')}
          busy={busy}
          hasData={!!uploadedName}
          onPreviewDeploy={
            schema && uploadedName ? () => setDeployModal('preview') : undefined
          }
          onDeployNow={
            schema && uploadedName ? () => setDeployModal('review') : undefined
          }
        />
      </main>

      {error && (
        <div className="fixed bottom-5 left-1/2 -translate-x-1/2 rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-[12.5px] text-red-700 shadow-card">
          {error}
          <button className="ml-3 text-red-500 hover:text-red-700" onClick={() => setError(null)}>
            ×
          </button>
        </div>
      )}

      {deployModal && workspaceId && uploadedName && (
        <DeployModal
          workspaceId={workspaceId}
          csvFilename={uploadedName}
          mode={deployModal}
          onClose={() => setDeployModal(null)}
          schema={schema}
          validation={validation}
          score={score}
          onOpenStarterQueries={() => {
            setDeployModal(null);
            setStarterQueriesOpen(true);
          }}
          onLoadMoreData={() => {
            setDeployModal(null);
            setTimeout(() => setDeployModal('review'), 50);
          }}
          onDeployCompleted={async (graphName, counts) => {
            const total = Object.values(counts).reduce((a, b) => a + (b ?? 0), 0);
            const parts = Object.entries(counts).map(([v, c]) => `${v}=${c ?? 0}`).join(', ');
            const msg =
              total > 0
                ? `Deployed to ${graphName}: ${parts}. Total ${total.toLocaleString()} rows loaded.`
                : `Deployed schema to ${graphName} (${(schema?.vertices.length ?? 0)} vertices, ${(schema?.edges.length ?? 0)} edges). No data loaded yet.`;
            if (workspaceId) {
              setMessages((prev) => [
                ...prev,
                { role: 'agent', content: msg, type: 'answer' as ChatMessage['type'] },
              ]);
              try {
                await api.appendChatEvent(workspaceId, msg, { type: 'progress' });
              } catch {
                /* best-effort */
              }
            }
          }}
        />
      )}

      {starterQueriesOpen && workspaceId && uploadedName && (
        <StarterQueriesPanel
          workspaceId={workspaceId}
          csvFilename={uploadedName}
          onClose={() => setStarterQueriesOpen(false)}
          onQueriesGenerated={async (count, validated) => {
            const msg = `Generated ${count} starter queries (${validated} validated against the live graph).`;
            setMessages((prev) => [
              ...prev,
              { role: 'agent', content: msg, type: 'answer' as ChatMessage['type'] },
            ]);
            if (workspaceId) {
              try {
                await api.appendChatEvent(workspaceId, msg, { type: 'progress' });
              } catch {
                /* best-effort */
              }
            }
          }}
          onQueryInstalled={async (queryName) => {
            const msg = `Installed query "${queryName}" in TigerGraph.`;
            setMessages((prev) => [
              ...prev,
              { role: 'agent', content: msg, type: 'answer' as ChatMessage['type'] },
            ]);
            if (workspaceId) {
              try {
                await api.appendChatEvent(workspaceId, msg, { type: 'progress' });
              } catch {
                /* best-effort */
              }
            }
          }}
        />
      )}
    </div>
  );
}
