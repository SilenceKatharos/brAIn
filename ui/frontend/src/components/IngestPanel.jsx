import React, { useEffect, useRef, useState } from 'react'

export function IngestPanel({ onClose, onComplete }) {
  const [projectName, setProjectName] = useState('')
  const [folderPath, setFolderPath] = useState('')
  const [jobId, setJobId] = useState(null)
  const [job, setJob] = useState(null)
  const [error, setError] = useState(null)
  const outputRef = useRef(null)
  const pollRef = useRef(null)

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [])

  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight
    }
  }, [job?.output])

  function startPolling(id) {
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`/api/ingest/${id}`)
        const data = await r.json()
        setJob(data)
        if (data.status !== 'running') {
          clearInterval(pollRef.current)
        }
      } catch {
        clearInterval(pollRef.current)
      }
    }, 2000)
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    setJob({ status: 'running', output: '' })
    try {
      const r = await fetch('/api/ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_name: projectName, folder_path: folderPath }),
      })
      if (!r.ok) {
        const msg = await r.text()
        throw new Error(msg)
      }
      const { job_id } = await r.json()
      setJobId(job_id)
      startPolling(job_id)
    } catch (err) {
      setError(err.message)
      setJob(null)
    }
  }

  const isRunning = job?.status === 'running'
  const isDone = job?.status === 'done'
  const isError = job?.status === 'error'

  return (
    <div className="ingest-overlay" onClick={e => e.target === e.currentTarget && !isRunning && onClose()}>
      <div className="ingest-panel">
        <div className="ingest-header">
          <span>Ingest project</span>
          {!isRunning && (
            <button className="ingest-close" onClick={onClose}>✕</button>
          )}
        </div>

        {!job ? (
          <form className="ingest-form" onSubmit={handleSubmit}>
            <label className="ingest-label">
              Project name
              <input
                className="ingest-input"
                placeholder="e.g. monai"
                value={projectName}
                onChange={e => setProjectName(e.target.value)}
                pattern="[a-zA-Z0-9_\-]+"
                required
              />
              <span className="ingest-hint">alphanumeric, underscores, hyphens</span>
            </label>
            <label className="ingest-label">
              Folder path
              <input
                className="ingest-input"
                placeholder="/home/user/my-project"
                value={folderPath}
                onChange={e => setFolderPath(e.target.value)}
                required
              />
              <span className="ingest-hint">absolute path to the project directory</span>
            </label>
            {error && <div className="ingest-error">{error}</div>}
            <div className="ingest-actions">
              <button type="button" className="ingest-btn secondary" onClick={onClose}>Cancel</button>
              <button type="submit" className="ingest-btn primary">Launch ingestion</button>
            </div>
          </form>
        ) : (
          <div className="ingest-status-wrap">
            <div className="ingest-status-bar">
              <div className={`ingest-badge ${job.status}`}>
                {isRunning && <span className="ingest-spinner" />}
                {isRunning ? 'Running…' : isDone ? 'Done' : 'Error'}
              </div>
              {jobId && <span className="ingest-jobid">job {jobId}</span>}
            </div>

            <pre className="ingest-output" ref={outputRef}>
              {job.output || (isRunning ? 'Waiting for Claude…' : '')}
            </pre>

            {!isRunning && (
              <div className="ingest-actions">
                <button className="ingest-btn secondary" onClick={onClose}>Close</button>
                {isDone && (
                  <button
                    className="ingest-btn primary"
                    onClick={() => { onComplete(); onClose() }}
                  >
                    Refresh graph
                  </button>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
