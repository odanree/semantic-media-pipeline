'use client'

import { useEffect, useRef, useState } from 'react'
import VideoPlayer from './VideoPlayer'
import HighlightReelPlayer from './HighlightReelPlayer'

const STREAM_BASE = process.env.NEXT_PUBLIC_STREAM_URL || 'http://localhost:8000'

type SortKey = 'similarity' | 'energy'

interface SimilarResult {
  file_path: string
  file_type: string
  best_similarity: number
  best_timestamp?: number
  audio_rms_energy?: number | null
}

interface SourceResult {
  file_path: string
  file_type: string
  timestamp?: number
}

interface ReelState {
  playlistUrl: string
  clipCount: number
  totalDurationSec: number
}

interface SimilarPanelProps {
  source: SourceResult
  onClose: () => void
  label?: string
}

export default function SimilarPanel({ source, onClose, label }: SimilarPanelProps) {
  const [results, setResults] = useState<SimilarResult[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedVideo, setSelectedVideo] = useState<SimilarResult | null>(null)
  const [reel, setReel] = useState<ReelState | null>(null)
  const [reelOpen, setReelOpen] = useState(false)
  const [reelLoading, setReelLoading] = useState(false)
  const [reelError, setReelError] = useState<string | null>(null)
  const [limit, setLimit] = useState(20)
  const [sortKey, setSortKey] = useState<SortKey>('similarity')
  const [clipPadding, setClipPadding] = useState(3)
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    setResults([])
    setReel(null)
    setReelError(null)
    setClipPadding(3)
    fetch('/api/similar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        file_path: source.file_path,
        timestamp: source.timestamp,
        limit,
        threshold: 0.5,
        ...(label !== undefined && { label }),
      }),
    })
      .then((r) => {
        if (!r.ok) return r.json().then((e) => Promise.reject(e.error || 'Failed'))
        return r.json()
      })
      .then((data) => setResults(data.results ?? []))
      .catch((e) => setError(typeof e === 'string' ? e : 'Could not load similar videos'))
      .finally(() => setLoading(false))
  }, [source.file_path, source.timestamp, limit])

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  async function playReel() {
    const videoResults = results.filter((r) => r.file_type === 'video')
    if (videoResults.length === 0) return
    if (reel) { setReelOpen(true); return }

    setReelLoading(true)
    setReelError(null)
    const clips = videoResults.map((r) => {
      const ts = r.best_timestamp ?? 0
      return { file_path: r.file_path, start_sec: Math.max(0, ts - clipPadding), end_sec: ts + clipPadding }
    })
    try {
      const res = await fetch('/api/playlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clips }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: 'Unknown error' }))
        setReelError(err.error || 'Playlist generation failed')
        return
      }
      const data = await res.json()
      setReel({
        playlistUrl: `${STREAM_BASE}${data.playlist_url}`,
        clipCount: data.clip_count,
        totalDurationSec: data.total_duration_sec,
      })
      setReelOpen(true)
    } catch {
      setReelError('Network error — could not generate reel')
    } finally {
      setReelLoading(false)
    }
  }

  const filename = source.file_path.split('/').pop() ?? source.file_path
  const videoCount = results.filter((r) => r.file_type === 'video').length

  return (
    <>
      {/* Backdrop — click to close */}
      <div
        className="fixed inset-0 bg-black bg-opacity-40 z-40"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer */}
      <div
        ref={panelRef}
        className="fixed right-0 top-0 h-full w-[420px] max-w-full bg-gray-900 border-l border-gray-700 z-50 flex flex-col shadow-2xl"
        role="dialog"
        aria-label="Similar videos"
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-3 p-4 border-b border-gray-700 shrink-0">
          <div className="min-w-0">
            <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">Similar to</p>
            <p className="text-sm text-white font-medium truncate" title={filename}>{filename}</p>
            {source.timestamp != null && (
              <p className="text-xs text-gray-500 mt-0.5">@ {source.timestamp.toFixed(1)}s</p>
            )}
          </div>
          <button
            onClick={onClose}
            className="shrink-0 text-gray-400 hover:text-white transition text-lg leading-none mt-0.5"
            aria-label="Close similar panel"
          >
            ✕
          </button>
        </div>

        {/* Source thumbnail */}
        <div className="px-4 pt-3 pb-2 shrink-0">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={
              source.file_type === 'video'
                ? `${STREAM_BASE}/api/thumbnail?path=${encodeURIComponent(source.file_path)}&t=${source.timestamp ?? 0}`
                : `${STREAM_BASE}/api/stream?path=${encodeURIComponent(source.file_path)}`
            }
            alt="Source frame"
            className="w-full h-32 object-cover rounded-lg opacity-70"
          />
        </div>

        {/* Results */}
        <div className="flex-1 overflow-y-auto px-4 pb-4">
          {loading && (
            <div className="flex flex-col items-center justify-center h-40 gap-3 text-gray-400">
              <div className="w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
              <span className="text-sm">Finding similar…</span>
            </div>
          )}

          {error && (
            <div className="mt-4 text-center text-red-400 text-sm">{error}</div>
          )}

          {!loading && !error && results.length === 0 && (
            <div className="mt-8 text-center text-gray-500 text-sm">No similar videos found</div>
          )}

          {!loading && results.length > 0 && (
            <>
              {/* Controls row */}
              <div className="flex items-center gap-2 mb-3 flex-wrap">
                <p className="text-xs text-gray-500 mr-auto">
                  {results.length} {results.length === 1 ? 'result' : 'results'}
                </p>
                {/* Sort */}
                <select
                  value={sortKey}
                  onChange={(e) => setSortKey(e.target.value as SortKey)}
                  className="px-2 py-1 rounded text-xs bg-gray-700 text-gray-300 border border-gray-600 focus:outline-none cursor-pointer"
                  aria-label="Sort similar results"
                >
                  <option value="similarity">Similarity ↑</option>
                  <option value="energy">Energy ↑</option>
                </select>
                {/* Limit */}
                <div className="flex rounded overflow-hidden border border-gray-600">
                  {[20, 30, 50].map((n) => (
                    <button
                      key={n}
                      onClick={() => setLimit(n)}
                      className={`px-2 py-1 text-xs font-medium transition ${limit === n ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'}`}
                      aria-label={`Show ${n} results`}
                    >
                      {n}
                    </button>
                  ))}
                </div>
                {videoCount > 0 && (
                  <>
                    <div className="flex rounded overflow-hidden border border-gray-600" title="Clip padding per side">
                      {[3, 5, 10, 15].map((n) => (
                        <button
                          key={n}
                          onClick={() => { setClipPadding(n); setReel(null) }}
                          className={`px-1.5 py-1 text-xs font-medium transition ${clipPadding === n ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'}`}
                          aria-label={`${n}s clip padding`}
                          title={`±${n}s clip`}
                        >
                          ±{n}s
                        </button>
                      ))}
                    </div>
                    <button
                      onClick={playReel}
                      disabled={reelLoading}
                      className="px-3 py-1.5 rounded text-xs font-semibold transition bg-purple-700 hover:bg-purple-600 disabled:opacity-50 disabled:cursor-wait text-white"
                      aria-label="Play highlight reel of similar videos"
                    >
                      {reelLoading ? '⏳…' : `▶ Reel`}
                    </button>
                  </>
                )}
              </div>
              {reelError && <p className="text-xs text-red-400 mb-2">{reelError}</p>}
              <div className="grid grid-cols-2 gap-3">
                {[...results].sort((a, b) =>
                  sortKey === 'energy'
                    ? (b.audio_rms_energy ?? -1) - (a.audio_rms_energy ?? -1)
                    : b.best_similarity - a.best_similarity
                ).map((r) => (
                  <button
                    key={r.file_path}
                    onClick={() => r.file_type === 'video' && setSelectedVideo(r)}
                    className="group text-left bg-gray-800 rounded-lg overflow-hidden hover:ring-2 hover:ring-blue-500 transition focus:outline-none focus:ring-2 focus:ring-blue-500"
                    aria-label={`${r.file_path.split('/').pop()} — ${(r.best_similarity * 100).toFixed(1)}% similar`}
                  >
                    <div className="relative aspect-video bg-gray-700 overflow-hidden">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={
                          r.file_type === 'video'
                            ? `${STREAM_BASE}/api/thumbnail?path=${encodeURIComponent(r.file_path)}&t=${r.best_timestamp ?? 0}`
                            : `${STREAM_BASE}/api/stream?path=${encodeURIComponent(r.file_path)}`
                        }
                        alt={r.file_path.split('/').pop()}
                        className="w-full h-full object-cover"
                        loading="lazy"
                      />
                      {r.file_type === 'video' && (
                        <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity bg-black bg-opacity-30">
                          <div className="w-8 h-8 rounded-full bg-black bg-opacity-60 flex items-center justify-center">
                            <span className="text-white text-sm pl-0.5">▶</span>
                          </div>
                        </div>
                      )}
                      <div className="absolute bottom-1 right-1 px-1.5 py-0.5 bg-black bg-opacity-70 rounded text-xs font-semibold text-white">
                        {(r.best_similarity * 100).toFixed(1)}%
                      </div>
                    </div>
                    <div className="p-2">
                      <p className="text-xs text-gray-300 truncate">{r.file_path.split('/').pop()}</p>
                      {r.best_timestamp != null && (
                        <p className="text-xs text-gray-500 mt-0.5">@ {r.best_timestamp.toFixed(1)}s</p>
                      )}
                    </div>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {reel && reelOpen && (
        <HighlightReelPlayer
          playlistUrl={reel.playlistUrl}
          clipCount={reel.clipCount}
          totalDurationSec={reel.totalDurationSec}
          onClose={() => setReelOpen(false)}
        />
      )}

      {selectedVideo && (
        <VideoPlayer
          result={{
            file_path: selectedVideo.file_path,
            file_type: selectedVideo.file_type,
            similarity: selectedVideo.best_similarity,
            timestamp: selectedVideo.best_timestamp,
          }}
          onClose={() => setSelectedVideo(null)}
        />
      )}
    </>
  )
}
