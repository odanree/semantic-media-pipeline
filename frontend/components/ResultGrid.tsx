'use client'

import Image from 'next/image'
import VideoPlayer from './VideoPlayer'
import { useState, useEffect, useRef } from 'react'

interface SearchResult {
  file_path: string
  file_type: string
  similarity: number
  frame_index?: number
  timestamp?: number
}

interface ResultGridProps {
  results: SearchResult[]
}

type ViewMode = 'grid' | 'list'

export default function ResultGrid({ results }: ResultGridProps) {
  const [selectedVideo, setSelectedVideo] = useState<SearchResult | null>(null)
  const [currentPage, setCurrentPage] = useState(1)
  const [viewMode, setViewMode] = useState<ViewMode>('grid')
  const itemsPerPage = 20
  const totalPages = Math.ceil(results.length / itemsPerPage)

  // Calculate pagination
  const startIndex = (currentPage - 1) * itemsPerPage
  const endIndex = startIndex + itemsPerPage
  const currentResults = results.slice(startIndex, endIndex)

  // Reset to page 1 when results change
  useEffect(() => {
    setCurrentPage(1)
  }, [results])

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
          Showing {startIndex + 1}-{Math.min(endIndex, results.length)} of {results.length} results
          {totalPages > 1 && ` • Page ${currentPage} of ${totalPages}`}
        </div>

        <div className="flex gap-2">
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
            key={`${result.file_path}-${result.frame_index || 0}`}
            result={result}
            viewMode={viewMode}
            onSelect={() => result.file_type === 'video' && setSelectedVideo(result)}
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

      {selectedVideo && (
        <VideoPlayer
          result={selectedVideo}
          onClose={() => setSelectedVideo(null)}
        />
      )}
    </>
  )
}

// Lazy-loaded result item component
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
          if ((e.key === 'Enter' || e.key === ' ') && result.file_type === 'video') {
            onSelect()
          }
        }}
        tabIndex={result.file_type === 'video' ? 0 : -1}
        aria-label={`${result.file_type} with ${(result.similarity * 100).toFixed(1)}% similarity`}
      >
        <div className="relative aspect-square bg-gray-700 overflow-hidden">
          <div className="absolute inset-0 bg-gradient-to-b from-transparent to-gray-900 opacity-60 z-10"></div>

          {isVisible ? (
            result.file_type === 'video' ? (
              <div className="w-full h-full flex items-center justify-center">
                <div className="text-center">
                  <div className="text-4xl mb-2">🎥</div>
                  <p className="text-xs text-gray-300">Click to play</p>
                </div>
              </div>
            ) : (
              <div className="text-4xl">🖼️</div>
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
              ? `Frame ${result.frame_index} @ ${(result.timestamp || 0).toFixed(1)}s`
              : result.file_type === 'video'
              ? 'Video'
              : 'Image'}
          </p>
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
          if ((e.key === 'Enter' || e.key === ' ') && result.file_type === 'video') {
            onSelect()
          }
        }}
        tabIndex={result.file_type === 'video' ? 0 : -1}
        aria-label={`${result.file_type} with ${(result.similarity * 100).toFixed(1)}% similarity`}
      >
        {/* Thumbnail */}
        <div className="flex-shrink-0 w-24 h-24 bg-gray-700 rounded overflow-hidden relative">
          <div className="absolute inset-0 bg-gradient-to-b from-transparent to-gray-900 opacity-60"></div>
          {isVisible ? (
            result.file_type === 'video' ? (
              <div className="w-full h-full flex items-center justify-center text-2xl">🎥</div>
            ) : (
              <div className="w-full h-full flex items-center justify-center text-2xl">🖼️</div>
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
              ? `Frame ${result.frame_index} @ ${(result.timestamp || 0).toFixed(1)}s`
              : result.file_type === 'video'
              ? 'Full video'
              : 'Full image'}
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
