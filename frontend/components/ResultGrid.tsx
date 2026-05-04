'use client'

import VideoPlayer from './VideoPlayer'
import HighlightReelPlayer from './HighlightReelPlayer'
import SimilarPanel from './SimilarPanel'
import { useState, useEffect, useRef, useMemo } from 'react'
import { useVotes } from '@/hooks/useVotes'
import { useSearchQuery } from '@/context/SearchContext'

interface SearchResult {
  id: string
  file_path: string
  file_type: string
  similarity: number
  frame_index?: number
  timestamp?: number
  audio_segment_start_sec?: number | null
  audio_segment_end_sec?: number | null
  audio_segment_index?: number
  audio_rms_energy?: number | null
  user_vote?: 1 | -1 | null
  vote_label?: Record<string, number> | null
  vote_query?: string | null
}

interface ReelState {
  playlistUrl: string
  clipCount: number
  totalDurationSec: number
}

interface ResultGridProps {
  results: SearchResult[]
  availableLabels?: string[]
  excludeVoted?: boolean
  onExcludeVotedChange?: (val: boolean) => void
  onLimitChange?: (n: number) => void
}

type ViewMode = 'grid' | 'list'
type CardSize = 'small' | 'large'
type SortKey = 'similarity_desc' | 'similarity_asc' | 'rms_desc' | 'rms_asc'

// Stream directly from FastAPI - bypasses Next.js proxy, no Node.js buffering
const STREAM_BASE = process.env.NEXT_PUBLIC_STREAM_URL || 'http://localhost:8000'

export default function ResultGrid({ results, availableLabels, excludeVoted = false, onExcludeVotedChange, onLimitChange }: ResultGridProps) {
  const searchQuery = useSearchQuery()
  const [selectedVideo, setSelectedVideo] = useState<SearchResult | null>(null)
  const [selectedImage, setSelectedImage] = useState<SearchResult | null>(null)
  const [similarSource, setSimilarSource] = useState<SearchResult | null>(null)
  const [currentPage, setCurrentPage] = useState(1)
  const [viewMode, setViewMode] = useState<ViewMode>('grid')
  const [cardSize, setCardSize] = useState<CardSize>('small')
  const [sortKey, setSortKey] = useState<SortKey>('similarity_desc')
  const [reel, setReel] = useState<ReelState | null>(null)
  const [reelOpen, setReelOpen] = useState(false)
  const [reelLoading, setReelLoading] = useState(false)
  const [reelError, setReelError] = useState<string | null>(null)
  const [clipPadding, setClipPadding] = useState(3)
  const [sceneMode, setSceneMode] = useState(false)
  const [sceneThreshold, setSceneThreshold] = useState(0.75)
  const [sceneBoundsCache, setSceneBoundsCache] = useState<Record<string, { start_sec: number; end_sec: number }>>({})
  const [sceneBoundsLoading, setSceneBoundsLoading] = useState(false)
  const [voteLabelsOverride, setVoteLabelsOverride] = useState<Record<string, Record<string, number>>>({})
  const [itemsPerPage, setItemsPerPage] = useState(50)
  const [filenameFilter, setFilenameFilter] = useState('')

  // Seed initial votes from results
  const initialVotes = useMemo(() => {
    const votes: Record<string, 1 | -1> = {}
    results.forEach((r) => {
      if (r.user_vote === 1 || r.user_vote === -1) {
        const key = (r.audio_segment_index !== undefined && r.audio_segment_index !== null)
          ? `${r.file_path}#${r.audio_segment_index}`
          : r.file_path
        votes[key] = r.user_vote
      }
    })
    return votes
  }, [results])

  const { votes, vote, tagVote, getVoteKey } = useVotes({ initialVotes })

  const sortedResults = useMemo(() => [...results].sort((a, b) => {
    // Primary sort: fully query-scoped via vote_label[searchQuery].
    // +1 = boost, -1 = sink, absent = neutral. Cross-query votes don't affect ranking.
    const keyA = a.audio_segment_index !== undefined ? `${a.file_path}#${a.audio_segment_index}` : a.file_path
    const keyB = b.audio_segment_index !== undefined ? `${b.file_path}#${b.audio_segment_index}` : b.file_path
    const labelA = searchQuery ? ({ ...(a.vote_label ?? {}), ...(voteLabelsOverride[keyA] ?? {}) })[searchQuery] ?? 0 : 0
    const labelB = searchQuery ? ({ ...(b.vote_label ?? {}), ...(voteLabelsOverride[keyB] ?? {}) })[searchQuery] ?? 0 : 0
    const va = Math.sign(labelA)
    const vb = Math.sign(labelB)
    if (vb !== va) return vb - va

    // Secondary sort: selected sort key
    switch (sortKey) {
      case 'similarity_asc': return a.similarity - b.similarity
      // ↑ = highest first, ↓ = lowest first
      case 'rms_desc': return (b.audio_rms_energy ?? -1) - (a.audio_rms_energy ?? -1)
      case 'rms_asc':  return (a.audio_rms_energy ?? Infinity) - (b.audio_rms_energy ?? Infinity)
      default: return b.similarity - a.similarity
    }
  }), [results, sortKey, votes, voteLabelsOverride, searchQuery])

  const filteredResults = useMemo(() => {
    if (!filenameFilter.trim()) return sortedResults
    const q = filenameFilter.trim().toLowerCase()
    return sortedResults.filter((r) => r.file_path.split('/').pop()!.toLowerCase().includes(q))
  }, [sortedResults, filenameFilter])

  const totalPages = Math.ceil(filteredResults.length / itemsPerPage)

  // Calculate pagination
  const startIndex = (currentPage - 1) * itemsPerPage
  const endIndex = startIndex + itemsPerPage
  const currentResults = filteredResults.slice(startIndex, endIndex)

  // Reset to page 1 when results change; clear stale reel
  useEffect(() => {
    setCurrentPage(1)
    setSortKey('similarity_desc')
    setReel(null)
    setReelOpen(false)
    setReelError(null)
    setClipPadding(3)
    setSceneMode(false)
    setSceneBoundsCache({})
    setFilenameFilter('')
  }, [results])

  const currentVideoResults = currentResults.filter((r) => r.file_type === 'video')

  async function detectSceneBounds() {
    if (currentVideoResults.length === 0) return
    setSceneBoundsLoading(true)
    const settled = await Promise.allSettled(
      currentVideoResults.map(async (r) => {
        const res = await fetch('/api/scene-bounds', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file_path: r.file_path, timestamp: r.timestamp ?? 0, threshold: sceneThreshold }),
        })
        if (!res.ok) throw new Error(`${res.status}`)
        const data = await res.json()
        return { key: `${r.file_path}:::${r.timestamp ?? 0}`, bounds: { start_sec: data.start_sec, end_sec: data.end_sec } }
      })
    )
    const cache: Record<string, { start_sec: number; end_sec: number }> = {}
    for (const result of settled) {
      if (result.status === 'fulfilled') {
        cache[result.value.key] = result.value.bounds
      }
    }
    setSceneBoundsCache(prev => ({ ...prev, ...cache }))
    setSceneMode(true)
    setSceneBoundsLoading(false)
    setReel(null)  // invalidate cached reel — bounds changed
  }

  async function playHighlightReel() {
    if (currentVideoResults.length === 0) return
    // Reopen cached reel without recompiling
    if (reel) {
      setReelOpen(true)
      return
    }
    setReelLoading(true)
    setReelError(null)
    const activeCache = sceneMode ? sceneBoundsCache : undefined
    const clips = currentVideoResults.map((r) => ({
      file_path: r.file_path,
      ...clipBounds(r, clipPadding, activeCache),
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
    } catch {
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
          Showing {startIndex + 1}-{Math.min(endIndex, filteredResults.length)} of {filteredResults.length} results
          {filenameFilter && filteredResults.length !== sortedResults.length && ` (${sortedResults.length} total)`}
          {totalPages > 1 && ` • Page ${currentPage} of ${totalPages}`}
        </div>

        <div className="flex gap-2 flex-wrap items-center">
          <input
            type="text"
            value={filenameFilter}
            onChange={(e) => { setFilenameFilter(e.target.value); setCurrentPage(1) }}
            placeholder="Filter by filename…"
            className="px-2 py-2 rounded text-sm bg-gray-700 text-gray-300 border border-gray-600 hover:border-gray-500 focus:outline-none focus:border-blue-500 placeholder-gray-500 w-40"
            aria-label="Filter results by filename"
          />
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
            <>
              <select
                value={clipPadding}
                onChange={(e) => { setClipPadding(Number(e.target.value)); setReel(null) }}
                className="px-2 py-2 rounded text-sm bg-gray-700 text-gray-300 border border-gray-600 hover:border-gray-500 focus:outline-none cursor-pointer"
                aria-label="Clip padding seconds"
              >
                <option value={3}>±3s</option>
                <option value={5}>±5s</option>
                <option value={10}>±10s</option>
              </select>
              <select
                value={sceneThreshold}
                onChange={(e) => { setSceneThreshold(Number(e.target.value)); setSceneMode(false); setSceneBoundsCache({}); setReel(null) }}
                className="px-2 py-2 rounded text-sm bg-gray-700 text-gray-300 border border-gray-600 hover:border-gray-500 focus:outline-none cursor-pointer"
                title="Scene similarity threshold — lower = wider scenes"
              >
                <option value={0.65}>sim 0.65</option>
                <option value={0.70}>sim 0.70</option>
                <option value={0.75}>sim 0.75</option>
                <option value={0.80}>sim 0.80</option>
                <option value={0.85}>sim 0.85</option>
                <option value={0.90}>sim 0.90</option>
              </select>
              <button
                onClick={sceneMode ? () => { setSceneMode(false); setReel(null) } : detectSceneBounds}
                disabled={sceneBoundsLoading}
                className={`px-3 py-2 rounded text-sm font-semibold transition disabled:opacity-50 disabled:cursor-wait ${
                  sceneMode
                    ? 'bg-cyan-700 hover:bg-cyan-600 text-white'
                    : 'bg-gray-700 hover:bg-gray-600 text-gray-300'
                }`}
                title={sceneMode ? 'Scene mode ON — click to revert to ±padding' : 'Detect scene boundaries from frame similarity'}
              >
                {sceneBoundsLoading ? '⏳ Detecting…' : sceneMode ? '≈ Scene ON' : '≈ Scene'}
              </button>
              <button
                onClick={playHighlightReel}
                disabled={reelLoading}
                className="px-3 py-2 rounded text-sm font-semibold transition bg-purple-700 hover:bg-purple-600 disabled:opacity-50 disabled:cursor-wait text-white"
                aria-label="Play highlight reel of all video results"
              >
                {reelLoading ? '⏳ Compiling…' : `▶ Reel (${currentVideoResults.length})`}
              </button>
            </>
          )}
          {onExcludeVotedChange && (
            <button
              onClick={() => onExcludeVotedChange(!excludeVoted)}
              className={`px-3 py-2 rounded text-sm font-semibold transition ${
                excludeVoted
                  ? 'bg-yellow-600 text-white'
                  : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              }`}
              title={excludeVoted ? 'Showing unvoted only — click to show all' : 'Show unvoted only'}
            >
              {excludeVoted ? '○ Unvoted' : '○ All'}
            </button>
          )}
          <select
            value={itemsPerPage}
            onChange={(e) => {
              setItemsPerPage(Number(e.target.value))
              setCurrentPage(1)
            }}
            className="px-2 py-2 rounded text-sm font-semibold bg-gray-700 text-gray-300 border border-gray-600 cursor-pointer"
            title="Results per page"
          >
            <option value={25}>25</option>
            <option value={50}>50</option>
            <option value={75}>75</option>
            <option value={100}>100</option>
            <option value={200}>200</option>
          </select>
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
          {viewMode === 'grid' && (
            <>
              <button
                onClick={() => setCardSize('small')}
                className={`px-3 py-2 rounded text-sm font-semibold transition ${
                  cardSize === 'small'
                    ? 'bg-blue-600 text-white'
                    : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                }`}
                title="Small cards (4 per row)"
              >
                S
              </button>
              <button
                onClick={() => setCardSize('large')}
                className={`px-3 py-2 rounded text-sm font-semibold transition ${
                  cardSize === 'large'
                    ? 'bg-blue-600 text-white'
                    : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                }`}
                title="Large cards (2 per row)"
              >
                L
              </button>
            </>
          )}
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
            ? cardSize === 'large'
              ? 'grid grid-cols-1 sm:grid-cols-2 gap-4'
              : 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4'
            : 'space-y-3'
        }
        role="list"
      >
        {currentResults.map((result) => (
          <ResultItem
            key={result.id}
            result={result}
            viewMode={viewMode}
            clipPadding={clipPadding}
            sceneBoundsCache={sceneMode ? sceneBoundsCache : undefined}
            votes={votes}
            getVoteKey={getVoteKey}
            onVote={vote}
            searchQuery={searchQuery}
            voteLabelsOverride={voteLabelsOverride}
            onVoteLabelChange={(key, query, score) => setVoteLabelsOverride(prev => ({
              ...prev,
              [key]: { ...(prev[key] ?? {}), [query]: score },
            }))}
            onTagVote={(filePath, audioSegmentIndex, keyword) => {
              tagVote(filePath, audioSegmentIndex, keyword)
              const key = getVoteKey(filePath, audioSegmentIndex)
              setVoteLabelsOverride(prev => ({
                ...prev,
                [key]: { ...(prev[key] ?? {}), [keyword]: 1 },
              }))
            }}
            onSelect={() => {
              if (result.file_type === 'video') setSelectedVideo(result)
              else setSelectedImage(result)
            }}
            onFindSimilar={() => setSimilarSource(result)}
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
            {itemsPerPage} per page
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

      {similarSource && (
        <SimilarPanel
          source={{
            file_path: similarSource.file_path,
            file_type: similarSource.file_type,
            timestamp: similarSource.timestamp,
          }}
          onClose={() => setSimilarSource(null)}
          availableLabels={availableLabels}
          onTagVote={(filePath, audioSegmentIndex, keyword) => {
            const key = getVoteKey(filePath, audioSegmentIndex)
            setVoteLabelsOverride(prev => ({
              ...prev,
              [key]: { ...(prev[key] ?? {}), [keyword]: 1 },
            }))
          }}
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

function clipBounds(
  r: SearchResult,
  padding = 3,
  cache?: Record<string, { start_sec: number; end_sec: number }>,
): { start_sec: number; end_sec: number } {
  if (cache) {
    const cached = cache[`${r.file_path}:::${r.timestamp ?? 0}`]
    if (cached) return cached
  }
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
    : { start_sec: Math.max(0, ts - padding), end_sec: ts + padding }
}

function segmentDuration(
  result: SearchResult,
  padding: number,
  cache?: Record<string, { start_sec: number; end_sec: number }>,
): string | null {
  if (result.file_type !== 'video') return null
  const { start_sec, end_sec } = clipBounds(result, padding, cache)
  const secs = Math.round(end_sec - start_sec)
  if (secs < 60) return `${secs}s clip`
  return `${Math.floor(secs / 60)}m ${secs % 60}s clip`
}

function ResultItem({
  result,
  viewMode,
  clipPadding,
  sceneBoundsCache,
  votes,
  getVoteKey,
  onVote,
  onSelect,
  onFindSimilar,
  searchQuery,
  voteLabelsOverride,
  onVoteLabelChange,
  onTagVote,
}: {
  result: SearchResult
  viewMode: ViewMode
  clipPadding: number
  sceneBoundsCache?: Record<string, { start_sec: number; end_sec: number }>
  votes: Record<string, 1 | -1>
  getVoteKey: (filePath: string, audioSegmentIndex?: number) => string
  onVote: (filePath: string, audioSegmentIndex: number | undefined, direction: 1 | -1) => void
  onSelect: () => void
  onFindSimilar: () => void
  searchQuery: string
  voteLabelsOverride: Record<string, Record<string, number>>
  onVoteLabelChange: (key: string, query: string, score: number) => void
  onTagVote: (filePath: string, audioSegmentIndex: number | undefined, keyword: string) => void
}) {
  const [isVisible, setIsVisible] = useState(false)
  const [tagOpen, setTagOpen] = useState(false)
  const [tagValue, setTagValue] = useState('')
  const [hovered, setHovered] = useState(false)
  const [previewError, setPreviewError] = useState(false)
  const [proxySource, setProxySource] = useState<'proxy' | 'original' | null>(null)
  const [videoKey, setVideoKey] = useState(0)
  const [vlcState, setVlcState] = useState<'idle' | 'loading' | 'ok' | 'error'>('idle')
  const tagInputRef = useRef<HTMLInputElement>(null)
  const previewRef = useRef<HTMLVideoElement>(null)
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const proxyPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const shouldPollRef = useRef(false)

  // Clear polling intervals when the card unmounts (e.g., new search results render).
  // onMouseLeave only fires on explicit cursor exit — unmount leaves intervals orphaned.
  useEffect(() => {
    return () => {
      shouldPollRef.current = false
      if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current)
      if (proxyPollRef.current) clearInterval(proxyPollRef.current)
    }
  }, [])

  function openInVlc(e: React.MouseEvent) {
    e.stopPropagation()
    if (vlcState === 'loading') return
    setVlcState('loading')
    const t = result.timestamp ?? 0
    const url = `http://localhost:9876/open?path=${encodeURIComponent(result.file_path)}&t=${t.toFixed(3)}&sw=${window.screen.width}&sh=${window.screen.height}&ox=${window.screenX}&oy=${window.screenY}`
    fetch(url)
      .then(r => {
        setVlcState(r.ok ? 'ok' : 'error')
        setTimeout(() => setVlcState('idle'), r.ok ? 2000 : 3000)
      })
      .catch(() => {
        setVlcState('error')
        setTimeout(() => setVlcState('idle'), 3000)
      })
  }

  useEffect(() => {
    if (tagOpen && tagInputRef.current) tagInputRef.current.focus()
  }, [tagOpen])

  function openTag() {
    setTagValue(searchQuery || '')
    setTagOpen(true)
  }

  function submitTag() {
    const kw = tagValue.trim()
    if (!kw) return
    onTagVote(result.file_path, result.audio_segment_index, kw)
    setTagOpen(false)
  }

  const voteKey = getVoteKey(result.file_path, result.audio_segment_index)
  const isUpvoted = votes[voteKey] === 1
  const isDownvoted = votes[voteKey] === -1
  const effectiveVoteLabel = { ...(result.vote_label ?? {}), ...(voteLabelsOverride[voteKey] ?? {}) }
  const labelScore = searchQuery ? (effectiveVoteLabel[searchQuery] ?? null) : null
  const isCascade = isUpvoted && labelScore != null && labelScore < 1
  const isUnlabeled = isUpvoted && !isCascade && Object.keys(effectiveVoteLabel).length === 0
  const isManual = isUpvoted && !isCascade && !isUnlabeled

  // Badge priority:
  // 1. Any keyword set THIS session via voteLabelsOverride (includes # tags + upvotes)
  // 2. Current search query if it has score=1 in server-side vote_label
  // Cross-query server-side labels (from previous sessions) are intentionally hidden.
  const overrideLabel = voteLabelsOverride[voteKey] ?? {}
  const overrideKeyword = Object.entries(overrideLabel).find(([, v]) => v === 1)?.[0] ?? null
  const upvoteKeyword = isManual
    ? (overrideKeyword ?? (searchQuery && effectiveVoteLabel[searchQuery] === 1 ? searchQuery : null) ?? result.vote_query ?? null)
    : null

  function handleVote(dir: 1 | -1) {
    if (searchQuery) {
      onVoteLabelChange(voteKey, searchQuery, dir)
    }
    onVote(result.file_path, result.audio_segment_index, dir)
  }
  const cascadePct = isCascade ? Math.round(labelScore * 100) : null

  const upvoteCls = isManual
    ? 'bg-green-700 text-white'
    : isUnlabeled
    ? 'bg-lime-700 text-white'
    : isCascade
    ? 'bg-teal-900 text-teal-300 border border-teal-700'
    : 'bg-gray-700 text-gray-400 hover:bg-green-900 hover:text-green-300'
  const upvoteLabel = isCascade ? `👍 ${cascadePct}%` : '👍'
  const upvoteTitle = isManual ? 'Manually upvoted' : isUnlabeled ? 'Upvoted — no label attached' : isCascade ? `Auto-cascaded at ${cascadePct}% visual similarity` : 'Promote this result'
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
        className={`group cursor-pointer bg-gray-800 rounded-lg overflow-hidden transition ring-2 ${
          isManual ? 'ring-green-500' : isUnlabeled ? 'ring-lime-400' : isCascade ? 'ring-teal-600' : isDownvoted ? 'ring-red-900' : 'ring-transparent hover:ring-blue-500'
        }`}
        role="listitem"
        onClick={onSelect}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') onSelect()
        }}
        tabIndex={0}
        aria-label={`${result.file_type} with ${(result.similarity * 100).toFixed(1)}% similarity`}
      >
        <div
          className="relative aspect-square bg-gray-700 overflow-hidden"
          onMouseEnter={() => {
            shouldPollRef.current = true
            hoverTimerRef.current = setTimeout(() => {
              setHovered(true)
              setProxySource('original')
              const { start_sec, end_sec } = clipBounds(result, clipPadding, sceneBoundsCache)
              const statusUrl = `${STREAM_BASE}/api/proxy-status?path=${encodeURIComponent(result.file_path)}&start=${start_sec.toFixed(3)}&end=${end_sec.toFixed(3)}`
              // Initial check — guard against mouse leaving before fetch resolves
              fetch(statusUrl).then(r => r.json()).then(d => {
                if (!shouldPollRef.current) return
                if (d.status === 'proxy') { setProxySource('proxy'); return }
                // Proxy not ready yet — poll every 5s until it is, then reload the video
                proxyPollRef.current = setInterval(() => {
                  if (!shouldPollRef.current) {
                    clearInterval(proxyPollRef.current!); proxyPollRef.current = null
                    return
                  }
                  fetch(statusUrl).then(r => r.json()).then(d => {
                    if (!shouldPollRef.current) return
                    if (d.status === 'proxy') {
                      clearInterval(proxyPollRef.current!); proxyPollRef.current = null
                      setProxySource('proxy')
                      setVideoKey(k => k + 1)
                    }
                  }).catch(() => {})
                }, 5000)
              }).catch(() => {})
            }, 750)
          }}
          onMouseLeave={() => {
            shouldPollRef.current = false
            if (hoverTimerRef.current) { clearTimeout(hoverTimerRef.current); hoverTimerRef.current = null }
            if (proxyPollRef.current) { clearInterval(proxyPollRef.current); proxyPollRef.current = null }
            setHovered(false); setProxySource(null)
            if (previewRef.current) previewRef.current.pause()
          }}
        >
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
                  className={`w-full h-full object-cover transition-opacity duration-150 ${hovered ? 'opacity-0' : 'opacity-100'}`}
                  loading="lazy"
                />
                {hovered && !previewError && (() => {
                  const { start_sec, end_sec } = clipBounds(result, clipPadding, sceneBoundsCache)
                  // Proxy clips are re-encoded from 0; original files use absolute timestamps.
                  // Compute the relative playhead position within whatever is served.
                  const absTs = result.timestamp ?? 0
                  return (
                    <video
                      key={videoKey}
                      ref={previewRef}
                      src={`${STREAM_BASE}/api/stream?path=${encodeURIComponent(result.file_path)}&quality=proxy&start=${start_sec.toFixed(3)}&end=${end_sec.toFixed(3)}`}
                      muted
                      playsInline
                      className="absolute inset-0 w-full h-full object-cover"
                      onLoadedMetadata={() => {
                        const v = previewRef.current
                        if (!v) return
                        // Proxy clips are trimmed segments (duration ≈ end_sec - start_sec),
                        // so timestamps start at 0 — use relative offset.
                        // Original files retain full duration — use absolute timestamp.
                        const clipDur = end_sec - start_sec
                        const isProxyClip = v.duration <= clipDur + 2
                        const seekTo = isProxyClip ? Math.max(0, absTs - start_sec) : absTs
                        v.currentTime = seekTo < v.duration ? seekTo : 0
                        v.play()
                      }}
                      onTimeUpdate={() => {
                        const v = previewRef.current
                        if (!v) return
                        const clipDur = end_sec - start_sec
                        const isProxyClip = v.duration <= clipDur + 2
                        const loopFrom = isProxyClip ? Math.max(0, absTs - start_sec) : absTs
                        if (v.currentTime >= loopFrom + 10) v.currentTime = loopFrom
                      }}
                      onError={() => setPreviewError(true)}
                    />
                  )
                })()}
                {/* Play overlay — hidden while previewing */}
                {!hovered && (
                  <div className="absolute inset-0 z-20 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                    <div className="w-12 h-12 rounded-full bg-black bg-opacity-60 flex items-center justify-center">
                      <span className="text-white text-xl pl-0.5">▶</span>
                    </div>
                  </div>
                )}
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

          {/* Find Similar button — top-left, visible on hover */}
          <button
            className="absolute top-2 left-2 z-30 px-2 py-1 bg-black bg-opacity-70 rounded text-xs font-semibold text-blue-300 opacity-0 group-hover:opacity-100 transition-opacity hover:bg-opacity-90 hover:text-blue-200"
            onClick={(e) => { e.stopPropagation(); onFindSimilar() }}
            aria-label="Find similar videos"
            title="Find similar"
          >
            ≈ Similar
          </button>

          {/* VLC button — top-right, always visible on hover */}
          <button
            className={`absolute top-2 right-2 z-30 px-2 py-1 rounded text-xs font-semibold transition ${
              vlcState === 'error'   ? 'bg-red-900 text-red-300' :
              vlcState === 'loading' ? 'bg-orange-900 text-orange-300 cursor-wait' :
              vlcState === 'ok'      ? 'bg-green-900 text-green-300' :
              'bg-black bg-opacity-70 hover:bg-orange-700 text-gray-200 opacity-0 group-hover:opacity-100'
            }`}
            onClick={openInVlc}
            title={
              vlcState === 'error'   ? 'vlc-opener not running — start scripts/vlc-opener.py' :
              vlcState === 'loading' ? 'Opening…' :
              vlcState === 'ok'      ? 'Opened!' :
              'Open in VLC'
            }
          >
            {vlcState === 'loading' ? '⏳ VLC' : vlcState === 'ok' ? '✓ VLC' : vlcState === 'error' ? '✕ VLC' : 'VLC'}
          </button>

          <div className="absolute bottom-2 right-2 px-2 py-1 bg-black bg-opacity-70 rounded text-xs font-semibold z-20">
            {(result.similarity * 100).toFixed(1)}%
          </div>
          {/* Proxy/original badge — sibling of sim% so it renders above the video layer */}
          {hovered && proxySource && (
            <div className={`absolute bottom-2 left-2 z-30 px-1.5 py-0.5 rounded text-xs font-semibold ${
              proxySource === 'proxy'
                ? 'bg-blue-900 bg-opacity-80 text-blue-300'
                : 'bg-black bg-opacity-70 text-gray-300'
            }`}>
              {proxySource === 'proxy' ? 'PROXY' : 'ORIGINAL'}
            </div>
          )}
        </div>

        <div className="p-3">
          <p className="text-xs text-gray-400 truncate">{result.file_path.split('/').pop()}</p>
          <p className="text-xs text-gray-500 mt-1">
            {result.file_type === 'video' && result.frame_index !== undefined
              ? `Frame ${result.frame_index} @ ${(result.timestamp || 0).toFixed(1)}s${segmentDuration(result, clipPadding, sceneBoundsCache) ? ` · ${segmentDuration(result, clipPadding, sceneBoundsCache)}` : ''}`
              : result.file_type === 'video'
              ? 'Video'
              : 'Image'}
          </p>
          {result.file_type === 'video' && (() => {
            const bounds = clipBounds(result, clipPadding, sceneBoundsCache)
            const isScene = sceneBoundsCache != null && `${result.file_path}:::${result.timestamp ?? 0}` in sceneBoundsCache
            const aligned = !isScene && result.audio_segment_start_sec != null && bounds.start_sec === result.audio_segment_start_sec
            return (
              <p className={`text-xs mt-0.5 ${isScene ? 'text-cyan-400' : aligned ? 'text-blue-400' : 'text-yellow-500'}`}>
                reel: {bounds.start_sec.toFixed(1)}s – {bounds.end_sec.toFixed(1)}s
                {isScene && <span className="text-cyan-600 ml-1">≈scene</span>}
                {!isScene && result.audio_segment_start_sec != null && !aligned && (
                  <span className="text-gray-600 ml-1">(seg {result.audio_segment_start_sec.toFixed(1)}–{result.audio_segment_end_sec!.toFixed(1)})</span>
                )}
              </p>
            )
          })()}
          {upvoteKeyword && (
            <div className="mt-1">
              <span className="inline-block text-xs px-1.5 py-0.5 bg-green-900 text-green-400 rounded truncate max-w-full" title={upvoteKeyword}>
                {upvoteKeyword}
              </span>
            </div>
          )}
          {isUnlabeled && (
            <div className="mt-1">
              <span className="inline-block text-xs px-1.5 py-0.5 bg-lime-900 text-lime-400 rounded">
                no label
              </span>
            </div>
          )}
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
          {/* Vote buttons */}
          <div className="mt-1.5">
            {tagOpen ? (
              <div className="flex gap-1" onClick={e => e.stopPropagation()}>
                <input
                  ref={tagInputRef}
                  value={tagValue}
                  onChange={e => setTagValue(e.target.value)}
                  onKeyDown={e => {
                    e.stopPropagation()
                    if (e.key === 'Enter') submitTag()
                    else if (e.key === 'Escape') setTagOpen(false)
                  }}
                  className="flex-1 min-w-0 px-2 py-0.5 bg-gray-700 border border-indigo-500 rounded text-xs text-white focus:outline-none"
                  placeholder="keyword…"
                />
                <button onClick={e => { e.stopPropagation(); submitTag() }} className="px-1.5 py-0.5 bg-indigo-600 hover:bg-indigo-500 rounded text-xs text-white">✓</button>
                <button onClick={e => { e.stopPropagation(); setTagOpen(false) }} className="px-1.5 py-0.5 bg-gray-700 hover:bg-gray-600 rounded text-xs text-gray-400">✕</button>
              </div>
            ) : (
              <div className="flex gap-1">
                <button
                  onClick={(e) => { e.stopPropagation(); handleVote(1) }}
                  className={`flex-1 py-0.5 rounded text-xs font-semibold transition ${upvoteCls}`}
                  aria-label="Thumbs up"
                  title={upvoteTitle}
                >
                  {upvoteLabel}
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); handleVote(-1) }}
                  className={`flex-1 py-0.5 rounded text-xs font-semibold transition ${
                    isDownvoted ? 'bg-red-900 text-white' : 'bg-gray-700 text-gray-400 hover:bg-red-900 hover:text-red-300'
                  }`}
                  aria-label="Thumbs down"
                  title="Demote this result"
                >
                  👎
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); openTag() }}
                  className="px-1.5 py-0.5 rounded text-xs font-semibold bg-gray-700 text-gray-400 hover:bg-indigo-900 hover:text-indigo-300 transition"
                  title="Tag with custom keyword"
                >
                  #
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    )
  } else {
    return (
      <div
        ref={ref}
        className={`flex gap-4 p-4 bg-gray-800 rounded-lg transition cursor-pointer group ring-2 ${
          isManual ? 'ring-green-500' : isUnlabeled ? 'ring-lime-400' : isCascade ? 'ring-teal-600' : isDownvoted ? 'ring-red-900' : 'ring-transparent hover:ring-blue-500'
        }`}
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
              ? `Frame ${result.frame_index} @ ${(result.timestamp || 0).toFixed(1)}s${segmentDuration(result, clipPadding, sceneBoundsCache) ? ` · ${segmentDuration(result, clipPadding, sceneBoundsCache)}` : ''}`
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
          {upvoteKeyword && (
            <div className="mt-1">
              <span className="inline-block text-xs px-1.5 py-0.5 bg-green-900 text-green-400 rounded truncate max-w-xs" title={upvoteKeyword}>
                {upvoteKeyword}
              </span>
            </div>
          )}
          {isUnlabeled && (
            <div className="mt-1">
              <span className="inline-block text-xs px-1.5 py-0.5 bg-lime-900 text-lime-400 rounded">
                no label
              </span>
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex-shrink-0 flex flex-col items-center gap-2">
          {result.file_type === 'video' && (
            <div className="flex items-center justify-center w-10 h-10 rounded-full group-hover:bg-blue-600 transition">
              <span className="text-lg">▶︎</span>
            </div>
          )}
          <button
            className="opacity-0 group-hover:opacity-100 transition-opacity px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-xs text-blue-300 font-medium whitespace-nowrap"
            onClick={(e) => { e.stopPropagation(); onFindSimilar() }}
            aria-label="Find similar videos"
            title="Find similar"
          >
            ≈ Similar
          </button>
          {tagOpen ? (
            <div className="flex gap-1" onClick={e => e.stopPropagation()}>
              <input
                ref={tagInputRef}
                value={tagValue}
                onChange={e => setTagValue(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') submitTag()
                  else if (e.key === 'Escape') setTagOpen(false)
                }}
                className="w-28 px-2 py-1 bg-gray-700 border border-indigo-500 rounded text-xs text-white focus:outline-none"
                placeholder="keyword…"
              />
              <button onClick={e => { e.stopPropagation(); submitTag() }} className="px-2 py-1 bg-indigo-600 hover:bg-indigo-500 rounded text-xs text-white">✓</button>
              <button onClick={e => { e.stopPropagation(); setTagOpen(false) }} className="px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-xs text-gray-400">✕</button>
            </div>
          ) : (
            <div className="flex gap-1">
              <button
                onClick={(e) => { e.stopPropagation(); handleVote(1) }}
                className={`px-2 py-1 rounded text-xs font-semibold transition ${upvoteCls}`}
                aria-label="Thumbs up"
                title={upvoteTitle}
              >
                {upvoteLabel}
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); handleVote(-1) }}
                className={`px-2 py-1 rounded text-xs font-semibold transition ${
                  isDownvoted ? 'bg-red-900 text-white' : 'bg-gray-700 text-gray-400 hover:bg-red-900 hover:text-red-300'
                }`}
                aria-label="Thumbs down"
                title="Demote this result"
              >
                👎
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); openTag() }}
                className="px-2 py-1 rounded text-xs font-semibold bg-gray-700 text-gray-400 hover:bg-indigo-900 hover:text-indigo-300 transition"
                title="Tag with custom keyword"
              >
                #
              </button>
            </div>
          )}
        </div>
      </div>
    )
  }
}
