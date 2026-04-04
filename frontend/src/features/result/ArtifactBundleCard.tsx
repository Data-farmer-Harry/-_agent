import { useEffect, useMemo, useState } from 'react'

import { getArtifactText, resolveArtifactUrl } from '../../services/api'
import type { ArtifactRef, ClientSettings } from '../../types/api'

interface ArtifactBundleCardProps {
  settings: ClientSettings
  runId: string
  title: string
  statusLabel: string
  htmlContent?: string
  artifacts: ArtifactRef[]
  summary: Record<string, unknown>
}

function InlineResultFrame({ html }: { html: string }) {
  return (
    <div className="inline-result-frame-shell">
      <div className="inline-result-window">
        <div className="inline-result-window-bar">
          <div className="inline-result-window-dots" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
          <strong>相图结果窗口</strong>
          <span>图与数据都在这里展示</span>
        </div>
        <iframe srcDoc={html} title="Computed Result" sandbox="allow-scripts allow-same-origin" />
      </div>
    </div>
  )
}

function readMetrics(summary: Record<string, unknown>): Record<string, unknown> {
  const metrics = summary.metrics
  return metrics && typeof metrics === 'object' ? (metrics as Record<string, unknown>) : {}
}

export function ArtifactBundleCard({ settings, runId, title, statusLabel, htmlContent, artifacts, summary }: ArtifactBundleCardProps) {
  const [markdownText, setMarkdownText] = useState('')
  const markdownArtifact = useMemo(() => artifacts.find((artifact) => artifact.kind === 'markdown'), [artifacts])
  const metrics = readMetrics(summary)
  const imageArtifacts = useMemo(() => artifacts.filter((artifact) => artifact.kind === 'image' && !artifact.name.endsWith('.json')), [artifacts])
  const videoArtifacts = useMemo(() => artifacts.filter((artifact) => artifact.kind === 'video'), [artifacts])
  const downloadableArtifacts = useMemo(
    () => artifacts.filter((artifact) => ['csv', 'json', 'code', 'text'].includes(artifact.kind) || artifact.name.startsWith('uploaded_')),
    [artifacts],
  )

  useEffect(() => {
    let cancelled = false
    if (!markdownArtifact?.url) {
      setMarkdownText('')
      return
    }
    void getArtifactText(settings, markdownArtifact.url)
      .then((text) => {
        if (!cancelled) {
          setMarkdownText(text)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setMarkdownText('报告加载失败。')
        }
      })
    return () => {
      cancelled = true
    }
  }, [markdownArtifact, settings])

  return (
    <div className="artifact-bundle-card">
      <div className="artifact-bundle-header">
        <div>
          <strong>{title}</strong>
          <span>{runId}</span>
        </div>
        <span>{statusLabel}</span>
      </div>

      {htmlContent ? <InlineResultFrame html={htmlContent} /> : null}

      {Object.keys(metrics).length ? (
        <div className="artifact-metrics-grid">
          {Object.entries(metrics).map(([key, value]) => (
            <div key={key} className="artifact-metric-card">
              <span>{key.replace(/_/g, ' ')}</span>
              <strong>{typeof value === 'number' ? value.toFixed(3) : String(value)}</strong>
            </div>
          ))}
        </div>
      ) : null}

      {imageArtifacts.length ? (
        <div className="artifact-media-grid">
          {imageArtifacts.map((artifact) => (
            <figure key={artifact.name} className="artifact-media-card">
              <img src={resolveArtifactUrl(settings, artifact.url || artifact.path)} alt={artifact.name} />
              <figcaption>{artifact.name}</figcaption>
            </figure>
          ))}
        </div>
      ) : null}

      {videoArtifacts.length ? (
        <div className="artifact-video-grid">
          {videoArtifacts.map((artifact) => (
            <div key={artifact.name} className="artifact-video-card">
              <video src={resolveArtifactUrl(settings, artifact.url || artifact.path)} controls loop />
              <span>{artifact.name}</span>
            </div>
          ))}
        </div>
      ) : null}

      {markdownArtifact ? (
        <div className="artifact-markdown-card">
          <div className="artifact-section-title">Report</div>
          <pre>{markdownText || '加载报告中…'}</pre>
        </div>
      ) : null}

      {downloadableArtifacts.length ? (
        <div className="artifact-downloads-row">
          {downloadableArtifacts.map((artifact) => (
            <a
              key={artifact.name}
              href={resolveArtifactUrl(settings, artifact.url || artifact.path)}
              target="_blank"
              rel="noreferrer"
              download
              className="artifact-download-button"
            >
              {artifact.name}
            </a>
          ))}
        </div>
      ) : null}
    </div>
  )
}
