'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Background,
  BackgroundVariant,
  type Edge as FlowEdge,
  Handle,
  MarkerType,
  type Node as FlowNode,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
} from '@xyflow/react';
import { X } from 'lucide-react';
import type { Edge, Schema, Vertex } from '@/lib/types';

const NODE_SIZE = 64;

// Savanna-style palette — colored circles per vertex type.
const PALETTE = [
  '#4A90D9', // blue        — Customer / Party
  '#C5A3D8', // lavender    — Transaction
  '#E89034', // orange      — Card
  '#29BCBA', // teal        — Account
  '#DC4D43', // red         — Address
  '#4FB061', // green       — Phone
  '#A1BBDD', // light blue  — Email
  '#888A8F', // gray        — Device
  '#C5B824', // olive       — IP
  '#DD7DA8', // pink        — Merchant
  '#F0B98D', // peach
  '#B5DEB1', // light green
  '#8B5A3C', // brown
  '#7A6FE0', // indigo
];

function colorFor(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

/**
 * Radial / hub-and-spoke layout matching TigerGraph Savanna's graph viewer.
 *
 * Algorithm:
 * 1. Compute degree (in + out) for every vertex.
 * 2. Pick the highest-degree vertex as the hub center (ring 0).
 * 3. BFS outward — each vertex gets a "ring number" = its shortest-path
 *    distance from the center.
 * 4. Within each ring, distribute vertices evenly around the circle.
 *    Vertices that are connected to a ring-0 neighbor cluster near it.
 *
 * This produces the classic hub-with-satellites look seen in Savanna,
 * not the rigid top-down tree dagre produces.
 */
function radialLayout(schema: Schema): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const RING_RADIUS = 230;

  // 1. degree
  const degree: Record<string, number> = {};
  const adj: Record<string, Set<string>> = {};
  for (const v of schema.vertices) {
    degree[v.name] = 0;
    adj[v.name] = new Set();
  }
  for (const e of schema.edges) {
    if (!(e.from_vertex in degree)) continue;
    if (!(e.to_vertex in degree)) continue;
    degree[e.from_vertex]++;
    degree[e.to_vertex]++;
    adj[e.from_vertex].add(e.to_vertex);
    adj[e.to_vertex].add(e.from_vertex);
  }

  // 2. pick highest-degree vertex as center (tie-break by name for stability)
  const sortedByDegree = [...schema.vertices].sort((a, b) => {
    if (degree[b.name] !== degree[a.name]) return degree[b.name] - degree[a.name];
    return a.name.localeCompare(b.name);
  });
  const center = sortedByDegree[0]?.name ?? schema.vertices[0]?.name ?? '';

  // 3. BFS rings
  const ring: Record<string, number> = { [center]: 0 };
  const queue: string[] = [center];
  while (queue.length > 0) {
    const cur = queue.shift()!;
    for (const nb of adj[cur] ?? []) {
      if (!(nb in ring)) {
        ring[nb] = ring[cur] + 1;
        queue.push(nb);
      }
    }
  }
  // any disconnected vertices get pushed to a fallback ring
  for (const v of schema.vertices) {
    if (!(v.name in ring)) ring[v.name] = 99;
  }

  // 4. group by ring + assign positions
  const groups: Record<number, string[]> = {};
  for (const v of schema.vertices) {
    const r = ring[v.name];
    if (!groups[r]) groups[r] = [];
    groups[r].push(v.name);
  }

  const positions: Record<string, { x: number; y: number }> = {};
  for (const [ringNumStr, names] of Object.entries(groups)) {
    const ringNum = Number(ringNumStr);
    if (ringNum === 0) {
      // center vertex (or vertices, if tied) — put first at (0,0), small offset for any others
      names.forEach((name, i) => {
        positions[name] = i === 0
          ? { x: 0, y: 0 }
          : { x: i * 90, y: 0 };
      });
      continue;
    }
    if (ringNum === 99) {
      // disconnected — drop them below
      names.forEach((name, i) => {
        positions[name] = { x: (i - names.length / 2) * 120, y: 600 };
      });
      continue;
    }
    const radius = ringNum * RING_RADIUS;
    // Order siblings so that vertices connected to the same parent end up next
    // to each other — keeps related entities clustered visually.
    const ordered = [...names].sort((a, b) => {
      // try to group by their first-ring-back neighbor name
      const parentA = [...adj[a]].find((n) => ring[n] < ringNum);
      const parentB = [...adj[b]].find((n) => ring[n] < ringNum);
      if (parentA && parentB && parentA !== parentB) {
        return parentA.localeCompare(parentB);
      }
      return a.localeCompare(b);
    });
    // Offset the starting angle so ring 1 begins at the top
    const startAngle = ringNum === 1 ? -Math.PI / 2 : 0;
    ordered.forEach((name, i) => {
      const angle = startAngle + (i / ordered.length) * 2 * Math.PI;
      positions[name] = {
        x: Math.cos(angle) * radius,
        y: Math.sin(angle) * radius,
      };
    });
  }

  const nodes: FlowNode[] = schema.vertices.map((v) => {
    const p = positions[v.name] ?? { x: 0, y: 0 };
    return {
      id: v.name,
      type: 'vertex',
      position: { x: p.x - NODE_SIZE / 2, y: p.y - NODE_SIZE / 2 },
      data: {
        label: v.name,
        primaryId: v.primary_id,
        attrCount: v.attributes.length,
        color: colorFor(v.name),
        isHub: v.name === center,
      },
    };
  });

  const edges: FlowEdge[] = schema.edges.map((e, i) => {
    const color = colorFor(e.from_vertex);
    return {
      id: `e${i}-${e.name}`,
      source: e.from_vertex,
      target: e.to_vertex,
      label: edgeVerb(e.name),
      // bezier curves look much better in radial layout than straight lines
      type: 'default',
      labelStyle: {
        fontSize: 10,
        fill: '#CFD2D7',
        fontWeight: 600,
        letterSpacing: '0.04em',
      },
      labelBgStyle: { fill: 'transparent' },
      style: { stroke: color, strokeWidth: 1.5, opacity: 0.75 },
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 14, height: 14 },
      animated: false,
    };
  });

  return { nodes, edges };
}

// "Customer_OWNS_Account" → "OWNS"  (just the verb in the middle)
function edgeVerb(name: string): string {
  const parts = name.split('_');
  if (parts.length <= 2) return name.toUpperCase();
  // Drop first (source vertex) and last (target vertex) → keep only the verb
  return parts.slice(1, -1).join('_').toUpperCase() || name.toUpperCase();
}

interface VertexData {
  label: string;
  primaryId: string;
  attrCount: number;
  color: string;
  isHub: boolean;
}

function VertexNode({ data, selected }: { data: VertexData; selected?: boolean }) {
  // Hub vertex gets a larger circle to emphasise it as the center of the graph
  const size = data.isHub ? NODE_SIZE + 14 : NODE_SIZE;
  return (
    <div className="flex flex-col items-center" style={{ width: size }}>
      <Handle
        type="target"
        position={Position.Top}
        style={{ background: data.color, border: 'none', width: 1, height: 1, opacity: 0 }}
      />
      <div
        style={{
          width: size,
          height: size,
          borderRadius: '50%',
          background: data.color,
          boxShadow: selected
            ? `0 0 0 3px rgba(255,255,255,0.25), 0 4px 14px ${data.color}88`
            : data.isHub
              ? `0 0 0 2px rgba(255,255,255,0.10), 0 6px 18px ${data.color}55`
              : `0 4px 14px ${data.color}33`,
          transition: 'box-shadow 0.15s ease',
        }}
      />
      <div
        className="pointer-events-none mt-1.5 max-w-[120px] truncate text-center text-[11px] font-medium"
        style={{ color: '#E8EAED' }}
        title={data.label}
      >
        {data.label}
      </div>
      <Handle
        type="source"
        position={Position.Bottom}
        style={{ background: data.color, border: 'none', width: 1, height: 1, opacity: 0 }}
      />
    </div>
  );
}

const nodeTypes = { vertex: VertexNode };

interface Props {
  schema: Schema;
}

type Selected =
  | { kind: 'vertex'; vertex: Vertex }
  | { kind: 'edge'; edge: Edge }
  | null;

function Inner({ schema }: Props) {
  const initial = useMemo(() => radialLayout(schema), [schema]);
  const [nodes, setNodes, onNodesChange] = useNodesState(initial.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initial.edges);
  const [selected, setSelected] = useState<Selected>(null);

  useEffect(() => {
    const next = radialLayout(schema);
    setNodes(next.nodes);
    setEdges(next.edges);
    // Clear selection if the previously-selected vertex/edge no longer exists
    setSelected((prev) => {
      if (!prev) return null;
      if (prev.kind === 'vertex' && !schema.vertices.some((v) => v.name === prev.vertex.name)) return null;
      if (prev.kind === 'edge' && !schema.edges.some((e) => e.name === prev.edge.name)) return null;
      return prev;
    });
  }, [schema, setNodes, setEdges]);

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: FlowNode) => {
      const v = schema.vertices.find((x) => x.name === node.id);
      if (v) setSelected({ kind: 'vertex', vertex: v });
    },
    [schema],
  );

  const handleEdgeClick = useCallback(
    (_: React.MouseEvent, fe: FlowEdge) => {
      // The edge id is `e<index>-<name>` (see radialLayout); fall back to label match
      const e = schema.edges.find((x) => fe.id.endsWith(`-${x.name}`)) ||
        schema.edges.find((x) => x.from_vertex === fe.source && x.to_vertex === fe.target);
      if (e) setSelected({ kind: 'edge', edge: e });
    },
    [schema],
  );

  const handlePaneClick = useCallback(() => setSelected(null), []);

  return (
    <div className="relative h-full w-full" style={{ background: '#17181C' }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        onEdgeClick={handleEdgeClick}
        onPaneClick={handlePaneClick}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        proOptions={{ hideAttribution: true }}
        minZoom={0.2}
        maxZoom={2}
      >
        <Background variant={BackgroundVariant.Dots} gap={28} size={1} color="#272A30" />
      </ReactFlow>
      {selected && (
        <RationaleCard selected={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}

function RationaleCard({
  selected,
  onClose,
}: {
  selected: Exclude<Selected, null>;
  onClose: () => void;
}) {
  const isVertex = selected.kind === 'vertex';
  const name = isVertex ? selected.vertex.name : selected.edge.name;
  const rationale = isVertex ? selected.vertex.rationale : selected.edge.rationale;
  const subtitle = isVertex
    ? `Vertex · primary_id: ${selected.vertex.primary_id} · ${selected.vertex.attributes.length} attrs`
    : `Edge · ${selected.edge.from_vertex} → ${selected.edge.to_vertex}`;

  return (
    <div className="absolute right-5 top-5 z-10 w-[320px] rounded-xl border border-tg-line bg-tg-card shadow-card">
      <div className="flex items-start justify-between gap-2 border-b border-tg-line px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="text-[10.5px] uppercase tracking-wide text-tg-mute">
            {isVertex ? 'Vertex' : 'Edge'}
          </div>
          <div className="truncate text-[14px] font-semibold text-tg-ink" title={name}>
            {name}
          </div>
          <div className="mt-0.5 text-[10.5px] text-tg-subtle">{subtitle}</div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-tg-mute hover:bg-tg-hover hover:text-tg-ink"
        >
          <X size={13} />
        </button>
      </div>
      <div className="px-4 py-3">
        <div className="mb-1 text-[10.5px] font-semibold uppercase tracking-wide text-tg-mute">
          Why this {isVertex ? 'vertex' : 'edge'}?
        </div>
        {rationale ? (
          <p className="whitespace-pre-wrap break-words text-[11.5px] leading-relaxed text-tg-ink">
            {rationale}
          </p>
        ) : (
          <p className="text-[11.5px] italic text-tg-subtle">No rationale captured.</p>
        )}

        {isVertex && selected.vertex.attributes.length > 0 && (
          <div className="mt-3 border-t border-tg-line pt-2.5">
            <div className="mb-1 text-[10.5px] font-semibold uppercase tracking-wide text-tg-mute">
              Attributes
            </div>
            <ul className="space-y-0.5 text-[11px]">
              {selected.vertex.attributes.map((a) => (
                <li key={a.name} className="flex justify-between gap-2">
                  <span className="truncate text-tg-ink" title={a.name}>
                    {a.name}
                  </span>
                  <span className="shrink-0 text-tg-subtle">{a.dtype}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

export default function SchemaGraph({ schema }: Props) {
  return (
    <ReactFlowProvider>
      <Inner schema={schema} />
    </ReactFlowProvider>
  );
}
