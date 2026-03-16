'use client'

import Image from 'next/image'
import VideoPlayer from './VideoPlayer'
import HighlightReelPlayer from './HighlightReelPlayer'
import { useState, useEffect, useRef, useMemo } from 'react'

interface SearchResult {
  id: string
  file_path: string
  file_type: string
  similarity: number
  frame_index?: number
  timestamp?: number
  audio_segment_start_sec?: number | null
  audio_segment_end_sec?: number | null
  audio_rms_energy?: number | null
}

interface ReelState {
  playlistUrl: string
  clipCount: number
  totalDurationSec: number
}

interface ResultGridProps {
  results: SearchResult[]
}

type ViewMode = 'grid' | 'list'
type SortKey = 'similarity_desc' | 'similarity_asc' | 'rms_desc' | 'rms_asc'

// Stream directly from FastAPI - bypasses Next.js proxy, no Node.js buffering
const STREAM_BASE = process.env.NEXT_PUBLIC_STREAM_URL || 'http://localhost:8000'

export default function ResultGrid({ results }: ResultGridProps) {
  const [selectedVideo, setSelectedVideo] = useState<SearchResult | null>(null)
  const [selectedImage, setSelectedImage] = useState<SearchResult | null>(null)
  const [currentPage, setCurrentPage] = useState(1)
  const [viewMode, setViewMode] = useState<ViewMode>('grid')
  const [sortKey, setSortKey] = useState<SortKey>('similarity_desc')
  const [reel, setReel] = useState<ReelState | null>(null)
  const [reelOpen, setReelOpen] = useState(false)
  const [reelLoading, setReelLoading] = useState(false)
  const [reelError, setReelError] = useState<string | null>(null)
  const itemsPerPage = 20

  const sortedResults = useMemo(() => [...results].sort((a, b) => {
    switch (sortKey) {
      case 'similarity_asc': return a.similarity - b.similarity
      // ↑ = highest first, ↓ = lowest first
      case 'rms_desc': return (b.audio_rms_energy ?? -1) - (a.audio_rms_energy ?? -1)
      case 'rms_asc':  return (a.audio_rms_energy ?? Infinity) - (b.audio_rms_energy ?? Infinity)
      default: return b.similarity - a.similarity
    }
  }), [results, sortKey])

  const totalPages = Math.ceil(sortedResults.length / itemsPerPage)

  // Calculate pagination
  const startIndex = (currentPage - 1) * itemsPerPage
  const endIndex = startIndex + itemsPerPage
  const currentResults = sortedResults.slice(startIndex, endIndex)

  // Reset to page 1 when results change; clear stale reel
  useEffect(() => {
    setCurrentPage(1)
    setSortKey('similarity_desc')
    setReel(null)
    setReelOpen(false)
    setReelError(null)
  }, [results])

  const currentVideoResults = currentResults.filter((r) => r.file_type === 'video')

  async function playHighlightReel() {
    if (currentVideoResults.length === 0) return
    // Reopen cached reel without recompiling
    if (reel) {
      setReelOpen(true)
      return
    }
    setReelLoading(true)
    setReelError(null)
    const clips = currentVideoResults.map((r) => ({
      file_path: r.file_path,
      ...clipBounds(r),
    }))
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
      const streamBase = process.env.NEXT_PUBLIC_STREAM_URL || 'http://localhost:8000'
      setReel({
        playlistUrl: `${streamBase}${data.playlist_url}`,
        clipCount: data.clip_count,
        totalDurationSec: data.total_duration_sec,
      })
      setReelOpen(true)
    } catch (e) {
      setReelError('Network error — could not generate reel')
    } finally {
      setReelLoading(false)
    }
  }

  // Scroll to top of results on page change
  useEffect(() => {
    const gridElement = document.getElementById('result-grid')
    if (gridElement) {
      gridElement.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [currentPage])

  if (results.length === 0) {
    return (
      <div className="text-center p-12 text-gray-400">
        <p className="text-lg">No results to display</p>
      </div>
    )
  }

  return (
    <>
      {/* View Mode Toggle + Pagination Info */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <div className="text-sm text-gray-400">
          Showing {startIndex + 1}-{Math.min(endIndex, sortedResults.length)} of {sortedResults.length} results
          {totalPages > 1 && ` • Page ${currentPage} of ${totalPages}`}
        </div>

        <div className="flex gap-2 flex-wrap items-center">
          <select
            value={sortKey}
            onChange={(e) => { setSortKey(e.target.value as SortKey); setCurrentPage(1) }}
            className="px-2 py-2 rounded text-sm bg-gray-700 text-gray-300 border border-gray-600 hover:border-gray-500 focus:outline-none cursor-pointer"
            aria-label="Sort results"
          >
            <option value="similarity_desc">Similarity ↑</option>
            <option value="similarity_asc">Similarity ↓</option>
            <option value="rms_desc">Energy ↑</option>
            <option value="rms_asc">Energy ↓</option>
          </select>
          {currentVideoResults.length > 0 && (
            <button
              onClick={playHighlightReel}
              disabled={reelLoading}
              className="px-3 py-2 rounded text-sm font-semibold transition bg-purple-700 hover:bg-purple-600 disabled:opacity-50 disabled:cursor-wait text-white"
              aria-label="Play highlight reel of all video results"
            >
              {reelLoading ? '⏳ Compiling…' : `▶ Reel (${currentVideoResults.length})`}
            </button>
          )}
          <button
            onClick={() => setViewMode('grid')}
            className={`px-3 py-2 rounded text-sm font-semibold transition ${
              viewMode === 'grid'
                ? 'bg-blue-600 text-white'
                : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
            }`}
            aria-label="Grid view"
          >
            ⊞ Grid
          </button>
          <button
            onClick={() => setViewMode('list')}
            className={`px-3 py-2 rounded text-sm font-semibold transition ${
              viewMode === 'list'
                ? 'bg-blue-600 text-white'
                : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
            }`}
            aria-label="List view"
          >
            ☰ List
          </button>
        </div>
        {reelError && (
          <p className="text-xs text-red-400 mt-1">{reelError}</p>
        )}
      </div>

      {/* Results Grid/List */}
      <div
        id="result-grid"
        className={
          viewMode === 'grid'
            ? 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4'
            : 'space-y-3'
        }
        role="list"
      >
        {currentResults.map((result) => (
          <ResultItem
            key={result.id}
            result={result}
            viewMode={viewMode}
            onSelect={() => {
              if (result.file_type === 'video') setSelectedVideo(result)
              else setSelectedImage(result)
            }}
          />
        ))}
      </div>

      {/* Pagination Controls */}
      {totalPages > 1 && (
        <div className="mt-8 flex flex-col items-center gap-4">
          <div className="flex gap-2 flex-wrap justify-center">
            {/* Previous Button */}
            <button
              onClick={() => setCurrentPage(Math.max(1, currentPage - 1))}
              disabled={currentPage === 1}
              className="px-3 py-2 bg-gray-700 hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded text-sm font-semibold transition"
              aria-label="Previous page"
            >
              ← Previous
            </button>

            {/* Page Numbers */}
            {Array.from({ length: totalPages }, (_, i) => i + 1)
              .filter((page) => {
                // Show first, last, and pages within 2 of current
                if (page === 1 || page === totalPages) return true
                return Math.abs(page - currentPage) <= 1
              })
              .map((page, idx, arr) => (
                <div key={page}>
                  {idx > 0 && arr[idx - 1] !== page - 1 && (
                    <span className="px-2 text-gray-500">…</span>
                  )}
                  <button
                    onClick={() => setCurrentPage(page)}
                    className={`px-3 py-2 rounded text-sm font-semibold transition ${
                      page === currentPage
                        ? 'bg-blue-600 text-white'
                        : 'bg-gray-700 hover:bg-gray-600 text-white'
                    }`}
                    aria-label={`Go to page ${page}`}
                    aria-current={page === currentPage ? 'page' : undefined}
                  >
                    {page}
                  </button>
                </div>
              ))}

            {/* Next Button */}
            <button
              onClick={() => setCurrentPage(Math.min(totalPages, currentPage + 1))}
              disabled={currentPage === totalPages}
              className="px-3 py-2 bg-gray-700 hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded text-sm font-semibold transition"
              aria-label="Next page"
            >
              Next →
            </button>
          </div>

          {/* Page Size Info */}
          <div className="text-xs text-gray-500">
            Showing {itemsPerPage} results per page
          </div>
        </div>
      )}

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
          result={selectedVideo}
          onClose={() => setSelectedVideo(null)}
        />
      )}

      {selectedImage && (
        <div
          className="fixed inset-0 bg-black bg-opacity-90 z-50 flex items-center justify-center p-4"
          onClick={() => setSelectedImage(null)}
        >
          <div
            className="relative max-w-5xl max-h-full flex flex-col items-center"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => setSelectedImage(null)}
              className="absolute -top-8 right-0 text-white text-sm hover:text-gray-300 transition"
            >
              ✕ Close
            </button>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={`${STREAM_BASE}/api/stream?path=${encodeURIComponent(selectedImage.file_path)}`}
              alt={selectedImage.file_path.split('/').pop()}
              className="max-w-full max-h-[80vh] object-contain rounded shadow-2xl"
            />
            <p className="mt-3 text-sm text-gray-400 text-center">
              {selectedImage.file_path.split('/').pop()}
              <span className="ml-3 text-blue-400 font-semibold">
                {(selectedImage.similarity * 100).toFixed(1)}% match
              </span>
            </p>
          </div>
        </div>
      )}
    </>
  )
}

// Lazy-loaded result item component
const CLIP_PADDING = 3

function clipBounds(r: SearchResult): { start_sec: number; end_sec: number } {
  const ts = r.timestamp ?? 0
  // Only use audio segment if it actually contains the matched timestamp.
  // The nearest VAD segment can be thousands of seconds away from the visual match.
  const contained =
    r.audio_segment_start_sec != null &&
    r.audio_segment_end_sec != null &&
    r.audio_segment_start_sec <= ts &&
    ts <= r.audio_segment_end_sec
  return contained
    ? { start_sec: r.audio_segment_start_sec!, end_sec: r.audio_segment_end_sec! }
    : { start_sec: Math.max(0, ts - CLIP_PADDING), end_sec: ts + CLIP_PADDING }
}

function segmentDuration(result: SearchResult): string | null {
  if (result.file_type !== 'video') return null
  const { start_sec, end_sec } = clipBounds(result)
  const secs = Math.round(end_sec - start_sec)
  if (secs < 60) return `${secs}s clip`
  return `${Math.floor(secs / 60)}m ${secs % 60}s clip`
}

function ResultItem({
  result,
  viewMode,
  onSelect,
}: {
  result: SearchResult
  viewMode: ViewMode
  onSelect: () => void
}) {
  const [isVisible, setIsVisible] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true)
          observer.unobserve(ref.current!)
        }
      },
      { rootMargin: '50px' }
    )

    if (ref.current) {
      observer.observe(ref.current)
    }

    return () => observer.disconnect()
  }, [])

  if (viewMode === 'grid') {
    return (
      <div
        ref={ref}
        className="group cursor-pointer bg-gray-800 rounded-lg overflow-hidden hover:ring-2 hover:ring-blue-500 transition"
        role="listitem"
        onClick={onSelect}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') onSelect()
        }}
        tabIndex={0}
        aria-label={`${result.file_type} with ${(result.similarity * 100).toFixed(1)}% similarity`}
      >
        <div className="relative aspect-square bg-gray-700 overflow-hidden">
          <div className="absolute inset-0 bg-gradient-to-b from-transparent to-gray-900 opacity-60 z-10"></div>

          {isVisible ? (
            result.file_type === 'video' ? (
              // Show the exact CLIP-matched frame as thumbnail (semantic thumbnail).
              // `result.timestamp` is the frame timestamp stored in the Qdrant
              // payload — the moment visually closest to the search query.
              <>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={`${STREAM_BASE}/api/thumbnail?path=${encodeURIComponent(result.file_path)}&t=${result.timestamp ?? 0}`}
                  alt={result.file_path.split('/').pop()}
                  className="w-full h-full object-cover"
                  loading="lazy"
                />
                {/* Play overlay */}
                <div className="absolute inset-0 z-20 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                  <div className="w-12 h-12 rounded-full bg-black bg-opacity-60 flex items-center justify-center">
                    <span className="text-white text-xl pl-0.5">▶</span>
                  </div>
                </div>
              </>
            ) : (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={`${STREAM_BASE}/api/stream?path=${encodeURIComponent(result.file_path)}`}
                alt={result.file_path.split('/').pop()}
                className="w-full h-full object-cover"
                loading="lazy"
              />
            )
          ) : (
            <div className="w-full h-full bg-gray-900 animate-pulse"></div>
          )}

          <div className="absolute bottom-2 right-2 px-2 py-1 bg-black bg-opacity-70 rounded text-xs font-semibold z-20">
            {(result.similarity * 100).toFixed(1)}%
          </div>
        </div>

        <div className="p-3">
          <p className="text-xs text-gray-400 truncate">{result.file_path.split('/').pop()}</p>
          <p className="text-xs text-gray-500 mt-1">
            {result.file_type === 'video' && result.frame_index !== undefined
              ? `Frame ${result.frame_index} @ ${(result.timestamp || 0).toFixed(1)}s${segmentDuration(result) ? ` · ${segmentDuration(result)}` : ''}`
              : result.file_type === 'video'
              ? 'Video'
              : 'Image'}
          </p>
          {result.file_type === 'video' && (() => {
            const bounds = clipBounds(result)
            const aligned = result.audio_segment_start_sec != null && bounds.start_sec === result.audio_segment_start_sec
            return (
              <p className={`text-xs mt-0.5 ${aligned ? 'text-blue-400' : 'text-yellow-500'}`}>
                reel: {bounds.start_sec.toFixed(1)}s – {bounds.end_sec.toFixed(1)}s
                {result.audio_segment_start_sec != null && !aligned && (
                  <span className="text-gray-600 ml-1">(seg {result.audio_segment_start_sec.toFixed(1)}–{result.audio_segment_end_sec!.toFixed(1)})</span>
                )}
              </p>
            )
          })()}
          {result.audio_rms_energy != null && (
            <div className="mt-1.5 flex items-center gap-1.5">
              <div className="flex-1 h-1 bg-gray-700 rounded-full overflow-hidden">
                <div
                  className="h-full bg-green-500 rounded-full"
                  style={{ width: `${Math.min(100, result.audio_rms_energy * 1000)}%` }}
                />
              </div>
              <span className="text-xs text-gray-500 shrink-0">
                {result.audio_rms_energy < 0.01 ? 'quiet' : result.audio_rms_energy < 0.04 ? 'low' : result.audio_rms_energy < 0.08 ? 'mid' : 'loud'}
              </span>
            </div>
          )}
        </div>
      </div>
    )
  } else {
    return (
      <div
        ref={ref}
        className="flex gap-4 p-4 bg-gray-800 rounded-lg hover:bg-gray-750 transition cursor-pointer group"
        role="listitem"
        onClick={onSelect}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') onSelect()
        }}
        tabIndex={0}
        aria-label={`${result.file_type} with ${(result.similarity * 100).toFixed(1)}% similarity`}
      >
        {/* Thumbnail */}
        <div className="flex-shrink-0 w-24 h-24 bg-gray-700 rounded overflow-hidden relative">
          <div className="absolute inset-0 bg-gradient-to-b from-transparent to-gray-900 opacity-60"></div>
          {isVisible ? (
            result.file_type === 'video' ? (
              <>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={`${STREAM_BASE}/api/thumbnail?path=${encodeURIComponent(result.file_path)}&t=${result.timestamp ?? 0}`}
                  alt={result.file_path.split('/').pop()}
                  className="w-full h-full object-cover"
                  loading="lazy"
                />
                <div className="absolute inset-0 flex items-center justify-center">
                  <div className="w-8 h-8 rounded-full bg-black bg-opacity-50 flex items-center justify-center">
                    <span className="text-white text-sm pl-0.5">▶</span>
                  </div>
                </div>
              </>
            ) : (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={`${STREAM_BASE}/api/stream?path=${encodeURIComponent(result.file_path)}`}
                alt={result.file_path.split('/').pop()}
                className="w-full h-full object-cover"
                loading="lazy"
              />
            )
          ) : (
            <div className="w-full h-full animate-pulse"></div>
          )}
          <div className="absolute bottom-1 right-1 px-1.5 py-0.5 bg-black bg-opacity-70 rounded text-xs font-semibold">
            {(result.similarity * 100).toFixed(0)}%
          </div>
        </div>

        {/* Details */}
        <div className="flex-1 min-w-0">
          <p className="font-semibold text-white truncate">
            {result.file_path.split('/').pop()}
          </p>
          <p className="text-sm text-gray-400 mt-1">{result.file_type.toUpperCase()}</p>
          <p className="text-xs text-gray-500 mt-1">
            {result.file_type === 'video' && result.frame_index !== undefined
              ? `Frame ${result.frame_index} @ ${(result.timestamp || 0).toFixed(1)}s${segmentDuration(result) ? ` · ${segmentDuration(result)}` : ''}`
              : result.file_type === 'video'
              ? 'Video'
              : 'Image'}
          </p>
          <div className="mt-2 flex items-center gap-2">
            <div className="flex-1 h-1.5 bg-gray-700 rounded-full overflow-hidden">
              <div
                className="h-full bg-blue-500"
                style={{ width: `${(result.similarity || 0) * 100}%` }}
              ></div>
            </div>
            <span className="text-xs text-blue-400 font-semibold whitespace-nowrap">
              {(result.similarity * 100).toFixed(1)}% match
            </span>
          </div>
          {result.audio_rms_energy != null && (
            <div className="mt-1 flex items-center gap-2">
              <div className="flex-1 h-1 bg-gray-700 rounded-full overflow-hidden">
                <div
                  className="h-full bg-green-500 rounded-full"
                  style={{ width: `${Math.min(100, result.audio_rms_energy * 1000)}%` }}
                />
              </div>
              <span className="text-xs text-gray-500 whitespace-nowrap">
                {result.audio_rms_energy < 0.01 ? 'quiet' : result.audio_rms_energy < 0.04 ? 'low' : result.audio_rms_energy < 0.08 ? 'mid' : 'loud'} energy
              </span>
            </div>
          )}

        </div>

        {/* Action Indicator */}
        {result.file_type === 'video' && (
          <div className="flex-shrink-0 flex items-center justify-center w-10 h-10 rounded-full group-hover:bg-blue-600 transition">
            <span className="text-lg">▶︎</span>
          </div>
        )}
      </div>
    )
  }
}
