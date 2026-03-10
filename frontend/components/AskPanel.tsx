'use client'

import { useState, FormEvent } from 'react'

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

export default function AskPanel() {
  const [question, setQuestion] = useState('')
  const [result, setResult] = useState<AskResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
        body: JSON.stringify({ question: question.trim() }),
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
      <form onSubmit={handleSubmit} className="flex gap-3">
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
          {result.sources.length > 0 && (
            <div>
              <h4 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">
                Retrieved Sources
              </h4>
              <div className="space-y-2">
                {result.sources.map((src, i) => (
                  <div
                    key={i}
                    className="bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 flex items-center gap-4 text-sm"
                  >
                    <span className="text-gray-500 font-mono w-6 shrink-0">[{i + 1}]</span>
                    <span className="text-blue-400 truncate flex-1 font-mono text-xs">
                      {src.file_path}
                    </span>
                    <span className="text-gray-500 shrink-0 capitalize">{src.file_type}</span>
                    {src.timestamp !== undefined && (
                      <span className="text-gray-500 shrink-0">{src.timestamp.toFixed(1)}s</span>
                    )}
                    <span className="text-green-400 shrink-0 font-medium">
                      {(src.similarity * 100).toFixed(0)}%
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
