import React, { useCallback, useEffect, useRef, useState } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  useReactFlow,
  ReactFlowProvider,
  MarkerType,
  Handle,
  Position,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCenter,
  forceCollide,
} from 'd3-force'

const TYPE_COLORS = {
  concept:   '#4c9be8',
  entity:    '#52c97c',
  algorithm: '#a78bfa',
  artifact:  '#f59e0b',
  mechanism: '#ef4444',
  property:  '#f472b6',
  event:     '#94a3b8',
  claim:     '#fb923c',
  person:    '#22d3ee',
  place:     '#a3e635',
  process:   '#34d399',
}

function typeColor(t) {
  return TYPE_COLORS[t] || '#888888'
}

function computeLayout(rawNodes, rawEdges, seedPositions = {}, width = 900, height = 650) {
  const simNodes = rawNodes.map(n => ({
    id: n['n.id'],
    importance: n['n.importance'] ?? 0.5,
    x: seedPositions[n['n.id']]?.x ?? (width / 2 + (Math.random() - 0.5) * 400),
    y: seedPositions[n['n.id']]?.y ?? (height / 2 + (Math.random() - 0.5) * 400),
  }))
  const idSet = new Set(simNodes.map(n => n.id))
  const simLinks = rawEdges
    .filter(e => idSet.has(e.src) && idSet.has(e.dst))
    .map(e => ({ source: e.src, target: e.dst }))

  const sim = forceSimulation(simNodes)
    .force('link', forceLink(simLinks).id(d => d.id).distance(140).strength(0.4))
    .force('charge', forceManyBody().strength(-250))
    .force('center', forceCenter(width / 2, height / 2))
    .force('collide', forceCollide(35))
    .stop()

  for (let i = 0; i < 400; i++) sim.tick()

  return Object.fromEntries(simNodes.map(n => [n.id, { x: n.x, y: n.y }]))
}

const HANDLE_STYLE = {
  width: 6, height: 6, background: 'transparent', border: 'none', opacity: 0,
}

function BrainNode({ data, selected }) {
  const color = typeColor(data.nodeType)
  const size = Math.round(16 + (data.importance ?? 0.5) * 28)
  return (
    <div className={`brain-node${selected ? ' selected' : ''}`}>
      <Handle type="target" position={Position.Top}    style={HANDLE_STYLE} />
      <Handle type="target" position={Position.Left}   style={HANDLE_STYLE} />
      <Handle type="source" position={Position.Bottom} style={HANDLE_STYLE} />
      <Handle type="source" position={Position.Right}  style={HANDLE_STYLE} />
      <div
        className="brain-node-circle"
        style={{
          width: size,
          height: size,
          background: color,
          boxShadow: selected
            ? `0 0 0 2px white, 0 0 12px ${color}88`
            : `0 0 8px ${color}44`,
        }}
      />
      <div className="brain-node-label" title={data.label}>
        {data.label.length > 18 ? data.label.slice(0, 16) + '…' : data.label}
      </div>
      {data.hiddenNeighbors > 0 && (
        <div
          className="brain-node-badge"
          title={`${data.hiddenNeighbors} hidden neighbor${data.hiddenNeighbors > 1 ? 's' : ''} — double-click to expand`}
        >
          +{data.hiddenNeighbors}
        </div>
      )}
    </div>
  )
}

const nodeTypes = { brain: BrainNode }

function Flow({ graphData, visibleNodeIds, selectedNodeId, onNodeSelect, onExpandNode }) {
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [layoutDone, setLayoutDone] = useState(false)
  const { fitView } = useReactFlow()
  // Persist node positions across expansions so existing nodes don't jump
  const positionsRef = useRef({})

  useEffect(() => {
    if (!graphData) return
    const { nodes: rawNodes, edges: rawEdges } = graphData

    const filteredNodes = visibleNodeIds
      ? rawNodes.filter(n => visibleNodeIds.has(n['n.id']))
      : rawNodes

    const idSet = new Set(filteredNodes.map(n => n['n.id']))
    const filteredEdges = rawEdges.filter(e => idSet.has(e.src) && idSet.has(e.dst))

    // Count hidden neighbors per visible node (to show the +N badge)
    const hiddenCount = new Map()
    rawEdges.forEach(e => {
      if (idSet.has(e.src) && !idSet.has(e.dst))
        hiddenCount.set(e.src, (hiddenCount.get(e.src) ?? 0) + 1)
      if (idSet.has(e.dst) && !idSet.has(e.src))
        hiddenCount.set(e.dst, (hiddenCount.get(e.dst) ?? 0) + 1)
    })

    const positions = computeLayout(filteredNodes, filteredEdges, positionsRef.current)
    positionsRef.current = positions

    const rfNodes = filteredNodes.map(n => ({
      id: n['n.id'],
      type: 'brain',
      position: positions[n['n.id']] ?? { x: 0, y: 0 },
      data: {
        label: n['n.label'],
        nodeType: n['n.type'],
        importance: n['n.importance'],
        description: n['n.description'],
        hiddenNeighbors: hiddenCount.get(n['n.id']) ?? 0,
      },
    }))

    const rfEdges = filteredEdges.map((e, i) => ({
      id: `e${i}-${e.src}-${e.dst}-${e['r.type']}`,
      source: e.src,
      target: e.dst,
      label: e['r.type'],
      labelStyle: { fill: '#7878a0', fontSize: 8 },
      labelBgStyle: { fill: '#13131f', fillOpacity: 0.8 },
      style: { stroke: '#5a5a80', strokeWidth: 1.5 },
      markerEnd: { type: MarkerType.ArrowClosed, color: '#5a5a80', width: 10, height: 10 },
      data: { confidence: e['r.confidence'], evidences: e['r.evidences'] },
    }))

    setNodes(rfNodes)
    setEdges(rfEdges)
    setLayoutDone(false)
    setTimeout(() => {
      fitView({ padding: 0.1, duration: 400 })
      setLayoutDone(true)
    }, 50)
  }, [graphData, visibleNodeIds])

  useEffect(() => {
    if (!layoutDone) return
    setNodes(nds =>
      nds.map(n => ({ ...n, selected: n.id === selectedNodeId }))
    )
  }, [selectedNodeId, layoutDone])

  const onNodeClick = useCallback((_, node) => {
    onNodeSelect(node.id)
  }, [onNodeSelect])

  const onNodeDoubleClick = useCallback((_, node) => {
    onExpandNode?.(node.id)
  }, [onExpandNode])

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={onNodeClick}
      onNodeDoubleClick={onNodeDoubleClick}
      nodeTypes={nodeTypes}
      fitView
      minZoom={0.05}
      maxZoom={3}
      colorMode="dark"
    >
      <Background color="#1e1e30" gap={24} size={1} />
      <Controls showInteractive={false} />
      <MiniMap
        nodeColor={n => typeColor(n.data?.nodeType)}
        nodeStrokeWidth={0}
        maskColor="rgba(0,0,0,0.6)"
        style={{ width: 120, height: 80 }}
      />
    </ReactFlow>
  )
}

export function GraphCanvas({ graphData, visibleNodeIds, loading, selectedNodeId, onNodeSelect, onExpandNode }) {
  return (
    <div className="canvas-wrap">
      {loading && (
        <div className="loading-overlay">Loading graph…</div>
      )}
      <ReactFlowProvider>
        <Flow
          graphData={graphData}
          visibleNodeIds={visibleNodeIds}
          selectedNodeId={selectedNodeId}
          onNodeSelect={onNodeSelect}
          onExpandNode={onExpandNode}
        />
      </ReactFlowProvider>
    </div>
  )
}
