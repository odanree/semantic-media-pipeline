'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { useVotes, getVoteKey } from '@/hooks/useVotes'
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
  user_vote?: 1 | -1 | null
  vote_label?: Record<string, number> | null
  best_frame_index?: number
  audio_segment_index?: number
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
  availableLabels?: string[]
  onTagVote?: (filePath: string, audioSegmentIndex: number | undefined, keyword: string) => void
}

export default function SimilarPanel({ source, onClose, availableLabels, onTagVote }: SimilarPanelProps) {
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
  const [labelFilter, setLabelFilter] = useState<string | undefined>(undefined)
  const [initialVotes, setInitialVotes] = useState<Record<string, 1 | -1>>({})
  const [labelChips, setLabelChips] = useState<{ keyword: string; sim: number }[]>([])
  const [selectedChip, setSelectedChip] = useState<string | null>(null)
  // Track which label was applied per key this session — drives ring color and badge
  const [taggedLabels, setTaggedLabels] = useState<Record<string, string>>({})
  const panelRef = useRef<HTMLDivElement>(null)
  // frame_index lookup: file_path+timestamp → frame_index, so votes target exact frame
  const frameIndexRef = useRef<Record<string, number>>({})

  // Downvotes are ephemeral — only reorder the reel, never persisted to backend.
  const { votes, vote, tagVote, getVoteKey } = useVotes({
    initialVotes,
    api: {
      persist: async (filePath, audioSegmentIndex, voteValue, searchQuery) => {
        if (voteValue === -1) return
        const fkey = audioSegmentIndex !== undefined ? `${filePath}#${audioSegmentIndex}` : filePath
        const frameIndex = frameIndexRef.current[fkey] ?? null
        const response = await fetch('/api/vote', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file_path: filePath, audio_segment_index: audioSegmentIndex, frame_index: frameIndex, vote: voteValue, search_query: searchQuery || null, cascade_threshold: 0.95 }),
        })
        if (!response.ok) throw new Error(`Vote API error: ${response.status}`)
      },
    },
  })

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
        ...(labelFilter !== undefined && { label: labelFilter }),
      }),
    })
      .then((r) => {
        if (!r.ok) return r.json().then((e) => Promise.reject(e.error || 'Failed'))
        return r.json()
      })
      .then((data) => {
        const newResults = data.results ?? []
        setResults(newResults)
        // Extract votes + frame indexes from results
        const votes: Record<string, 1 | -1> = {}
        const frameIdx: Record<string, number> = {}
        newResults.forEach((r: SimilarResult) => {
          if (r.user_vote === 1 || r.user_vote === -1) {
            const key = getVoteKey(r.file_path, r.audio_segment_index ?? undefined)
            votes[key] = r.user_vote
          }
          if (r.best_frame_index != null) {
            const fkey = getVoteKey(r.file_path, r.audio_segment_index ?? undefined)
            frameIdx[fkey] = r.best_frame_index
          }
        })
        setInitialVotes(votes)
        frameIndexRef.current = frameIdx
      })
      .catch((e) => setError(typeof e === 'string' ? e : 'Could not load similar videos'))
      .finally(() => setLoading(false))
  }, [source.file_path, source.timestamp, limit, labelFilter])

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  // Fetch top-k semantic label matches for the source frame via reverse lookup.
  useEffect(() => {
    setLabelChips([])
    fetch('/api/frame-labels', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_path: source.file_path, timestamp: source.timestamp, top_k: 8 }),
    })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.labels) setLabelChips(d.labels.map((l: { label: string; score: number }) => ({ keyword: l.label, sim: l.score }))) })
      .catch(() => {})
  }, [source.file_path, source.timestamp])

  const sortedResults = useMemo(() => [...results].sort((a, b) => {
    const keyA = a.audio_segment_index !== undefined ? `${a.file_path}#${a.audio_segment_index}` : a.file_path
    const keyB = b.audio_segment_index !== undefined ? `${b.file_path}#${b.audio_segment_index}` : b.file_path
    const va = votes[keyA] ?? 0
    const vb = votes[keyB] ?? 0
    if (vb !== va) return vb - va
    if (sortKey === 'energy') return (b.audio_rms_energy ?? -1) - (a.audio_rms_energy ?? -1)
    return b.best_similarity - a.best_similarity
  }), [results, sortKey, votes])

  function onVote(filePath: string, audioSegmentIndex: number | undefined, dir: 1 | -1) {
    vote(filePath, audioSegmentIndex, dir)
    setReel(null)
  }

  async function playReel() {
    const videoResults = sortedResults.filter((r) => r.file_type === 'video')
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
  const videoCount = sortedResults.filter((r) => r.file_type === 'video').length

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
          {/* Keyword chips — top vote_label keywords from similar results, click to tag-vote source */}
          {labelChips.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5 items-center">
              {labelChips.map(({ keyword, sim }) => {
                const isSelected = selectedChip === keyword
                return (
                  <button
                    key={keyword}
                    onClick={() => setSelectedChip(isSelected ? null : keyword)}
                    className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-xs transition ${
                      isSelected
                        ? 'bg-indigo-600 text-white ring-2 ring-indigo-400'
                        : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                    }`}
                    title={isSelected ? `Deselect "${keyword}"` : `Select "${keyword}" to tag results`}
                  >
                    <span className="truncate max-w-[100px]">{keyword}</span>
                    <span className={`shrink-0 ${isSelected ? 'text-indigo-300' : 'text-gray-500'}`}>{(sim * 100).toFixed(0)}%</span>
                  </button>
                )
              })}
            </div>
          )}
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
                {/* Label filter */}
                {availableLabels && availableLabels.length > 0 && (
                  <select
                    value={labelFilter ?? ''}
                    onChange={(e) => { setLabelFilter(e.target.value || undefined); setReel(null) }}
                    className="px-2 py-1 rounded text-xs bg-gray-700 text-gray-300 border border-gray-600 focus:outline-none cursor-pointer"
                    aria-label="Filter by label"
                  >
                    <option value="">Any label</option>
                    {availableLabels.map((l) => (
                      <option key={l} value={l}>{l}</option>
                    ))}
                  </select>
                )}
                {/* Limit */}
                <div className="flex rounded overflow-hidden border border-gray-600">
                  {[20, 30, 50, 75].map((n) => (
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
                {sortedResults.map((r) => {
                  const cardKey = getVoteKey(r.file_path, r.audio_segment_index)
                  const cardTaggedLabel = taggedLabels[cardKey] ?? null
                  return (
                  <div
                    key={cardKey}
                    className={`group relative text-left rounded-lg overflow-hidden transition ring-2 ${
                      votes[cardKey] === 1
                        ? (cardKey in taggedLabels || !(r.vote_label && Object.values(r.vote_label).every(v => v < 1)) ? 'ring-green-500 bg-gray-800' : 'ring-teal-600 bg-gray-800')
                        : votes[cardKey] === -1 ? 'ring-red-900 bg-gray-900 opacity-60'
                        : 'ring-transparent bg-gray-800 hover:ring-blue-500'
                    }`}
                  >
                    {/* Thumbnail — clickable to play */}
                    <button
                      className="w-full text-left focus:outline-none"
                      onClick={() => r.file_type === 'video' && setSelectedVideo(r)}
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
                        {cardTaggedLabel && (
                          <div className="absolute bottom-1 left-1 px-1.5 py-0.5 bg-green-700 bg-opacity-90 rounded text-xs font-semibold text-white truncate max-w-[80%]">
                            {cardTaggedLabel}
                          </div>
                        )}
                      </div>
                    </button>
                    <div className="p-2">
                      <p className="text-xs text-gray-300 truncate">{r.file_path.split('/').pop()}</p>
                      {r.best_timestamp != null && (
                        <p className="text-xs text-gray-500 mt-0.5">@ {r.best_timestamp.toFixed(1)}s</p>
                      )}
                      {r.audio_rms_energy != null && (
                        <div className="mt-1.5 flex items-center gap-1.5">
                          <div className="flex-1 h-1 bg-gray-700 rounded-full overflow-hidden">
                            <div
                              className="h-full bg-green-500 rounded-full"
                              style={{ width: `${Math.min(100, r.audio_rms_energy * 1000)}%` }}
                            />
                          </div>
                          <span className="text-xs text-gray-500 shrink-0">
                            {r.audio_rms_energy < 0.01 ? 'quiet' : r.audio_rms_energy < 0.04 ? 'low' : r.audio_rms_energy < 0.08 ? 'mid' : 'loud'}
                          </span>
                        </div>
                      )}
                      {/* Vote buttons */}
                      {(() => {
                        const key = getVoteKey(r.file_path, r.audio_segment_index)
                        const isUpvoted = votes[key] === 1
                        const isDownvoted = votes[key] === -1
                        const isTagged = key in taggedLabels
                        const labelValues = r.vote_label ? Object.values(r.vote_label) : []
                        const isCascade = isUpvoted && !isTagged && labelValues.length > 0 && labelValues.every(v => v < 1)
                        const isManual = isUpvoted && !isCascade
                        const topScore = isCascade ? Math.round(Math.max(...labelValues) * 100) : null
                        const upvoteCls = isManual
                          ? 'bg-green-700 text-white'
                          : isCascade
                          ? 'bg-teal-900 text-teal-300 border border-teal-700'
                          : 'bg-gray-700 text-gray-400 hover:bg-green-900 hover:text-green-300'
                        return (
                          <div className="mt-1.5 flex gap-1">
                            <button
                              disabled={!selectedChip}
                              onClick={() => {
                                if (!selectedChip) return
                                const key = getVoteKey(r.file_path, r.audio_segment_index)
                                tagVote(r.file_path, r.audio_segment_index, selectedChip)
                                setTaggedLabels(prev => ({ ...prev, [key]: selectedChip }))
                                onTagVote?.(r.file_path, r.audio_segment_index, selectedChip)
                                setReel(null)
                              }}
                              className={`flex-1 py-0.5 rounded text-xs font-semibold transition ${
                                selectedChip
                                  ? 'bg-indigo-700 text-white hover:bg-indigo-600'
                                  : 'bg-gray-800 text-gray-600 cursor-default'
                              }`}
                              title={selectedChip ? `Tag as "${selectedChip}"` : 'Select a label chip first'}
                            >
                              {selectedChip ? `🏷 ${selectedChip}` : '👍'}
                            </button>
                            <button
                              onClick={() => onVote(r.file_path, r.audio_segment_index, -1)}
                              className={`flex-1 py-0.5 rounded text-xs font-semibold transition ${
                                isDownvoted
                                  ? 'bg-red-900 text-white'
                                  : 'bg-gray-700 text-gray-400 hover:bg-red-900 hover:text-red-300'
                              }`}
                              aria-label="Thumbs down — demote in reel"
                              title="Demote in reel"
                            >
                              👎
                            </button>
                          </div>
                        )
                      })()}
                    </div>
                  </div>
                )
                })}
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
