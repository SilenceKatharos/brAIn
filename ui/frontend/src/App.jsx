import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { GraphCanvas } from './components/GraphCanvas.jsx'
import { NodePanel } from './components/NodePanel.jsx'
import { IngestPanel } from './components/IngestPanel.jsx'

const TYPE_COLORS = {
  concept:   'var(--type-concept)',
  entity:    'var(--type-entity)',
  algorithm: 'var(--type-algorithm)',
  artifact:  'var(--type-artifact)',
  mechanism: 'var(--type-mechanism)',
  property:  'var(--type-property)',
  event:     'var(--type-event)',
  claim:     'var(--type-claim)',
  person:    'var(--type-person)',
  place:     'var(--type-place)',
  process:   'var(--type-process)',
}

function typeColor(t) {
  return TYPE_COLORS[t] || 'var(--type-default)'
}


export default function App() {
  const [stats, setStats] = useState(null)
  const [graphData, setGraphData] = useState(null)
  const [graphLoading, setGraphLoading] = useState(true)
  const [types, setTypes] = useState([])
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [searchResults, setSearchResults] = useState(null)
  const [selectedNodeId, setSelectedNodeId] = useState(null)
  const [expandedNodeIds, setExpandedNodeIds] = useState(new Set())
  const [error, setError] = useState(null)
  const [showIngest, setShowIngest] = useState(false)

  useEffect(() => {
    fetch('/api/stats')
      .then(r => r.json())
      .then(setStats)
      .catch(() => setError('API unavailable — is the backend running?'))
  }, [])

  useEffect(() => {
    fetch('/api/types')
      .then(r => r.json())
      .then(setTypes)
      .catch(() => {})
  }, [])

  const fetchGraph = useCallback(() => {
    setGraphLoading(true)
    fetch('/api/graph')
      .then(r => r.json())
      .then(d => { setGraphData(d); setGraphLoading(false) })
      .catch(() => setGraphLoading(false))
  }, [])

  useEffect(() => { fetchGraph() }, [fetchGraph])

  useEffect(() => {
    const query = search.trim()
    if (!query && !typeFilter) {
      setSearchResults(null)
      return
    }
    const params = new URLSearchParams()
    if (query) params.set('q', query)
    if (typeFilter) params.set('node_type', typeFilter)
    params.set('limit', '80')
    fetch(`/api/search?${params}`)
      .then(r => r.json())
      .then(setSearchResults)
      .catch(() => {})
  }, [search, typeFilter])

  // Precompute undirected neighbor index (only rebuilds when graphData loads)
  const neighborMap = useMemo(() => {
    if (!graphData) return new Map()
    const map = new Map()
    graphData.edges.forEach(e => {
      if (!map.has(e.src)) map.set(e.src, new Set())
      if (!map.has(e.dst)) map.set(e.dst, new Set())
      map.get(e.src).add(e.dst)
      map.get(e.dst).add(e.src)
    })
    return map
  }, [graphData])

  // Derive the set of node IDs to show on canvas and in the sidebar list
  const visibleNodeIds = useMemo(() => {
    if (!graphData) return new Set()
    const allNodes = graphData.nodes // sorted by importance DESC from API

    let base
    if (searchResults !== null) {
      base = new Set(searchResults.map(n => n['n.id']))
    } else {
      // Default: show the highest-importance node for each project (identified by
      // a "project:*" source tag). allNodes is sorted by importance DESC so the
      // first node encountered per project tag is automatically the top one.
      base = new Set()
      const seenProjects = new Set()
      allNodes.forEach(n => {
        const tag = (n['n.sources'] || []).find(s => typeof s === 'string' && s.startsWith('project:'))
        if (tag && !seenProjects.has(tag)) {
          seenProjects.add(tag)
          base.add(n['n.id'])
        }
      })
      if (base.size === 0 && allNodes.length > 0) base.add(allNodes[0]['n.id'])
    }

    // Add 1-hop neighbors for every explicitly expanded node
    expandedNodeIds.forEach(id => {
      base.add(id)
      neighborMap.get(id)?.forEach(nid => base.add(nid))
    })

    return base
  }, [graphData, searchResults, expandedNodeIds, neighborMap])

  const handleExpandNode = useCallback((id) => {
    setExpandedNodeIds(prev => new Set([...prev, id]))
  }, [])

  const handleResetView = useCallback(() => {
    setExpandedNodeIds(new Set())
  }, [])

  // Select a node; if it's hidden, bring it into view automatically
  const handleNodeSelect = useCallback((id) => {
    setSelectedNodeId(id)
    if (id && !visibleNodeIds.has(id)) {
      setExpandedNodeIds(prev => new Set([...prev, id]))
    }
  }, [visibleNodeIds])

  const displayNodes = useMemo(() => {
    if (searchResults !== null) return searchResults
    if (!graphData) return []
    return graphData.nodes
      .filter(n => visibleNodeIds.has(n['n.id']))
      .map(n => ({
        'n.id':         n['n.id'],
        'n.label':      n['n.label'],
        'n.type':       n['n.type'],
        'n.importance': n['n.importance'],
      }))
  }, [graphData, searchResults, visibleNodeIds])

  const totalNodes = graphData?.nodes.length ?? 0

  return (
    <>
      <header className="header">
        <div className="header-logo">🧠 brAIn</div>
        <div className="header-stats">
          {stats && !stats.error ? (
            <>
              <div className="stat-chip"><span>{stats.total_nodes ?? 0}</span> nodes</div>
              <div className="stat-chip"><span>{stats.total_rels ?? 0}</span> relations</div>
              {stats.node_counts?.slice(0, 4).map(t => (
                <div key={t.type} className="stat-chip" style={{ borderColor: typeColor(t.type) }}>
                  <span style={{ color: typeColor(t.type) }}>{t.c}</span> {t.type}
                </div>
              ))}
            </>
          ) : null}
        </div>
        {error && <div className="header-error">{error}</div>}
        <button className="ingest-trigger-btn" onClick={() => setShowIngest(true)}>
          + Ingest project
        </button>
      </header>

      {showIngest && (
        <IngestPanel
          onClose={() => setShowIngest(false)}
          onComplete={() => {
            fetchGraph()
            fetch('/api/stats').then(r => r.json()).then(setStats).catch(() => {})
            fetch('/api/types').then(r => r.json()).then(setTypes).catch(() => {})
          }}
        />
      )}

      <div className="layout">
        <aside className="sidebar">
          <div className="sidebar-section">
            <input
              className="search-input"
              placeholder="Search nodes…"
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
            <div className="type-filters">
              {types.map(t => (
                <div
                  key={t.type}
                  className={`type-badge${typeFilter === t.type ? ' active' : ''}`}
                  style={{ color: typeColor(t.type), borderColor: typeColor(t.type) }}
                  onClick={() => setTypeFilter(typeFilter === t.type ? '' : t.type)}
                >
                  {t.type} {t.c}
                </div>
              ))}
            </div>
            {expandedNodeIds.size > 0 && (
              <button className="reset-view-btn" onClick={handleResetView}>
                ↺ Reset view
              </button>
            )}
          </div>

          <div className="node-list">
            {displayNodes.length === 0 ? (
              <div className="node-count">No nodes found</div>
            ) : (
              <>
                <div className="node-count">
                  {displayNodes.length}
                  {!searchResults && totalNodes > displayNodes.length && (
                    <span className="node-count-hint"> of {totalNodes} — double-click to expand</span>
                  )}
                  {searchResults && <span className="node-count-hint"> results</span>}
                </div>
                {displayNodes.map(n => (
                  <div
                    key={n['n.id']}
                    className={`node-item${selectedNodeId === n['n.id'] ? ' selected' : ''}`}
                    onClick={() => handleNodeSelect(n['n.id'])}
                  >
                    <div
                      className="node-dot"
                      style={{ background: typeColor(n['n.type']) }}
                    />
                    <div className="node-item-label" title={n['n.label']}>
                      {n['n.label']}
                    </div>
                    <div className="node-item-type">{n['n.type']}</div>
                  </div>
                ))}
              </>
            )}
          </div>
        </aside>

        <GraphCanvas
          graphData={graphData}
          visibleNodeIds={visibleNodeIds}
          loading={graphLoading}
          selectedNodeId={selectedNodeId}
          onNodeSelect={handleNodeSelect}
          onExpandNode={handleExpandNode}
        />

        <NodePanel
          nodeId={selectedNodeId}
          onNavigate={handleNodeSelect}
        />
      </div>
    </>
  )
}
