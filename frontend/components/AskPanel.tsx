'use client'

import { useState, FormEvent } from 'react'
import VideoPlayer from './VideoPlayer'

interface Source {
  file_path: string
  file_type: string
  similarity: number
  frame_index?: number
  timestamp?: number
  caption?: string
}

interface AskResult {
  question: string
  answer: string
  sources: Source[]
  model_used: string
  retrieval_count: number
  execution_time_ms: number
  scenes_collapsed: number
}

/** Parse all [N] citation numbers from the LLM answer text. */
function parseCitedIndices(answer: string): Set<number> {
  const matches = answer.matchAll(/\[(\d+)\]/g)
  return new Set([...matches].map(m => parseInt(m[1], 10)))
}

export default function AskPanel() {
  const [question, setQuestion] = useState('')
  const [dedup, setDedup] = useState(true)
  const [result, setResult] = useState<AskResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedVideo, setSelectedVideo] = useState<Source | null>(null)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!question.trim()) return

    setLoading(true)
    setError(null)
    setResult(null)

    try {
      const response = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: question.trim(), dedup }),
      })

      if (!response.ok) {
        const data = await response.json().catch(() => ({}))
        throw new Error((data as { error?: string }).error || 'Ask failed')
      }

      const data: AskResult = await response.json()
      setResult(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <form onSubmit={handleSubmit} className="space-y-3">
        <div className="flex gap-3">
          <input
            type="text"
            value={question}
            onChange={e => setQuestion(e.target.value)}
            placeholder='Ask anything — e.g. "What videos do I have from Vietnam?"'
            className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
            disabled={loading}
            aria-label="Question"
          />
          <button
            type="submit"
            disabled={loading || !question.trim()}
            className="bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white font-medium px-6 py-3 rounded-lg transition-colors"
          >
            Ask
          </button>
        </div>
        <label className="flex items-center gap-2 text-sm text-gray-400 cursor-pointer select-none w-fit">
          <input
            type="checkbox"
            checked={dedup}
            onChange={e => setDedup(e.target.checked)}
            className="accent-blue-500"
            aria-label="Collapse duplicate scenes"
          />
          Collapse duplicate scenes
        </label>
      </form>

      {loading && (
        <div className="text-center py-8">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-blue-400" />
          <p className="mt-4 text-gray-400">Searching your library and generating answer…</p>
        </div>
      )}

      {error && (
        <div
          className="p-4 bg-red-900 border border-red-700 rounded-lg text-red-100 flex justify-between items-center"
          role="alert"
        >
          <span>{error}</span>
          <button
            onClick={() => setError(null)}
            className="text-red-200 hover:text-white text-sm font-semibold"
          >
            ✕ Dismiss
          </button>
        </div>
      )}

      {result && (
        <div className="space-y-6">
          {/* Answer card */}
          <div className="bg-gray-800 border border-gray-700 rounded-xl p-6">
            <div className="flex items-start gap-3 mb-4">
              <span className="text-2xl shrink-0">🤖</span>
              <p className="text-white leading-relaxed whitespace-pre-wrap">{result.answer}</p>
            </div>
            <div className="flex flex-wrap gap-3 pt-4 border-t border-gray-700 text-xs text-gray-500">
              <span>Model: {result.model_used}</span>
              <span>·</span>
              <span>{result.retrieval_count} sources retrieved</span>
              <span>·</span>
              <span>{Math.round(result.execution_time_ms)}ms</span>
              {result.scenes_collapsed > 0 && (
                <>
                  <span>·</span>
                  <span>{result.scenes_collapsed} duplicate frames collapsed</span>
                </>
              )}
            </div>
          </div>

          {/* Sources */}
          {result.sources.length > 0 && (() => {
            const citedIndices = parseCitedIndices(result.answer)
            return (
              <div>
                <h4 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3 flex items-center gap-2">
                  Retrieved Sources
                  <span className="text-xs text-gray-500 font-normal">
                    ({result.sources.filter((_, i) => citedIndices.has(i + 1)).length} referenced)
                  </span>
                </h4>
                <div className="space-y-2">
                  {result.sources.map((src, i) => {
                    const isCited = citedIndices.has(i + 1)
                    const isVideo = src.file_type === 'video'
                    const filename = src.file_path.split('/').pop() ?? src.file_path
                    const thumbnailUrl = isVideo && src.timestamp !== undefined
                      ? `/api/thumbnail?path=${encodeURIComponent(src.file_path)}&t=${src.timestamp}`
                      : null
                    return (
                      <div
                        key={i}
                        onClick={isVideo ? () => setSelectedVideo(src) : undefined}
                        role={isVideo ? 'button' : undefined}
                        tabIndex={isVideo ? 0 : undefined}
                        onKeyDown={isVideo ? (e) => { if (e.key === 'Enter' || e.key === ' ') setSelectedVideo(src) } : undefined}
                        aria-label={isVideo ? `Play ${filename}` : undefined}
                        className={[
                          'rounded-lg px-4 py-3 flex items-center gap-3 text-sm transition-colors',
                          isCited
                            ? 'bg-blue-900/30 border border-blue-500'
                            : 'bg-gray-800 border border-gray-700',
                          isVideo
                            ? 'cursor-pointer hover:bg-gray-700 hover:border-gray-600'
                            : '',
                        ].join(' ')}
                      >
                        {/* Citation number */}
                        <span className={`font-mono w-6 shrink-0 text-center ${isCited ? 'text-blue-400 font-bold' : 'text-gray-500'}`}>
                          [{i + 1}]
                        </span>

                        {/* Thumbnail for video sources */}
                        {isVideo && thumbnailUrl && (
                          <div className="relative w-12 h-12 rounded overflow-hidden shrink-0 bg-gray-900 border border-gray-600">
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img
                              src={thumbnailUrl}
                              alt={filename}
                              className="w-full h-full object-cover"
                              loading="lazy"
                            />
                            {/* Play icon overlay */}
                            <div className="absolute inset-0 flex items-center justify-center bg-black/40">
                              <span className="text-white text-xs">▶</span>
                            </div>
                          </div>
                        )}

                        {/* Info section */}
                        <div className="flex-1 min-w-0">
                          <div className="text-blue-400 truncate font-mono text-xs">
                            {filename}
                          </div>
                          {src.caption && (
                            <div className="text-gray-400 text-xs truncate">
                              {src.caption}
                            </div>
                          )}
                        </div>

                        {/* Metadata */}
                        <div className="flex items-center gap-2 shrink-0">
                          <span className="text-gray-500 shrink-0 capitalize text-xs">{src.file_type}</span>
                          {src.timestamp !== undefined && (
                            <span className="text-gray-500 shrink-0 text-xs">{src.timestamp.toFixed(1)}s</span>
                          )}
                          <span className="text-green-400 shrink-0 font-medium text-xs">
                            {(src.similarity * 100).toFixed(0)}%
                          </span>
                          {isCited ? (
                            <span className="text-blue-400 shrink-0 text-xs font-semibold px-2 py-1 bg-blue-900/50 rounded">
                              ✓ cited
                            </span>
                          ) : (
                            <span className="text-gray-500 shrink-0 text-xs px-2 py-1">not cited</span>
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })()}
        </div>
      )}

      {selectedVideo && (
        <VideoPlayer
          result={selectedVideo}
          onClose={() => setSelectedVideo(null)}
        />
      )}
    </div>
  )
}
