import React from 'react'

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

export function NodePanel({ nodeId, onNavigate }) {
  const [detail, setDetail] = React.useState(null)
  const [loading, setLoading] = React.useState(false)

  React.useEffect(() => {
    if (!nodeId) { setDetail(null); return }
    setLoading(true)
    fetch(`/api/node/${encodeURIComponent(nodeId)}`)
      .then(r => r.json())
      .then(d => { setDetail(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [nodeId])

  if (!nodeId) {
    return (
      <div className="detail-panel">
        <div className="detail-empty">
          Click a node on the canvas<br />or in the list to inspect it.
        </div>
      </div>
    )
  }

  if (loading || !detail) {
    return (
      <div className="detail-panel">
        <div className="detail-empty">Loading…</div>
      </div>
    )
  }

  const color = typeColor(detail.type)
  const outgoing = detail.outgoing || []
  const incoming = detail.incoming || []

  return (
    <div className="detail-panel">
      <div className="detail-header">
        <div className="detail-label">{detail.label}</div>
        <div className="detail-meta">
          <span className="detail-type" style={{ background: color }}>
            {detail.type}
          </span>
          <span className="detail-importance">
            importance {(detail.importance ?? 0.5).toFixed(2)}
          </span>
        </div>
        <div className="detail-id">{detail.id}</div>
      </div>

      <div className="detail-body">
        {detail.description && (
          <div className="detail-section">
            <div className="detail-section-title">Description</div>
            <div className="detail-description">{detail.description}</div>
          </div>
        )}

        {detail.sources?.length > 0 && (
          <div className="detail-section">
            <div className="detail-section-title">Sources</div>
            <div className="detail-sources">
              {detail.sources.map(s => (
                <span key={s} className="source-chip">{s}</span>
              ))}
            </div>
          </div>
        )}

        {outgoing.length > 0 && (
          <div className="detail-section">
            <div className="detail-section-title">→ Outgoing ({outgoing.length})</div>
            {outgoing.map((r, i) => (
              <div key={i} className="rel-item">
                <div className="rel-type">{r.rel_type}</div>
                <div
                  className="rel-target"
                  onClick={() => onNavigate?.(r.dst)}
                >
                  {r.dst_label || r.dst}
                  {r.dst_type && (
                    <span style={{ color: typeColor(r.dst_type), marginLeft: 4, fontSize: 9 }}>
                      [{r.dst_type}]
                    </span>
                  )}
                </div>
                {r.factors?.filter(f => f).slice(0,1).map((f, i) => (
                  <div key={i} className="rel-factor">f= {f}</div>
                ))}
                {r.evidences?.filter(e => e).slice(0,1).map((e, i) => (
                  <div key={i} className="rel-evidence">{e}</div>
                ))}
                <div className="rel-confidence">confidence {(r.confidence ?? 0.8).toFixed(2)}</div>
              </div>
            ))}
          </div>
        )}

        {incoming.length > 0 && (
          <div className="detail-section">
            <div className="detail-section-title">← Incoming ({incoming.length})</div>
            {incoming.map((r, i) => (
              <div key={i} className="rel-item">
                <div className="rel-type">{r.rel_type}</div>
                <div
                  className="rel-target"
                  onClick={() => onNavigate?.(r.src)}
                >
                  {r.src_label || r.src}
                  {r.src_type && (
                    <span style={{ color: typeColor(r.src_type), marginLeft: 4, fontSize: 9 }}>
                      [{r.src_type}]
                    </span>
                  )}
                </div>
                {r.factors?.filter(f => f).slice(0,1).map((f, i) => (
                  <div key={i} className="rel-factor">f= {f}</div>
                ))}
                {r.evidences?.filter(e => e).slice(0,1).map((e, i) => (
                  <div key={i} className="rel-evidence">{e}</div>
                ))}
                <div className="rel-confidence">confidence {(r.confidence ?? 0.8).toFixed(2)}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
