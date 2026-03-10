'use client'

import { FormEvent, useState, useRef, useEffect } from 'react'
import { useSearchHistory } from '@/hooks/useSearchHistory'

export interface SearchFilters {
  fileType?: 'all' | 'images' | 'videos'
  fromDate?: string
  toDate?: string
  minSimilarity?: number
  maxResults?: number
  dedup?: boolean
}

interface SearchBarProps {
  onSearch: (query: string, filters: SearchFilters) => void
  isLoading?: boolean
  suggestions?: string[]
  externalQuery?: string
}

export default function SearchBar({ onSearch, isLoading = false, suggestions, externalQuery }: SearchBarProps) {
  const [query, setQuery] = useState('')
  const [showFilters, setShowFilters] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [filters, setFilters] = useState<SearchFilters>({
    fileType: 'all',
    minSimilarity: 0.3,
    maxResults: 20,
    dedup: true,
  })
  const { history, addToHistory } = useSearchHistory()
  const historyRef = useRef<HTMLDivElement>(null)
  const filterRef = useRef<HTMLDivElement>(null)

  // Sync externally-triggered query (e.g. tag pill clicks) into the input and history
  useEffect(() => {
    if (externalQuery && externalQuery !== query) {
      setQuery(externalQuery)
      addToHistory(externalQuery, filters)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalQuery])

  // Close dropdowns when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (historyRef.current && !historyRef.current.contains(e.target as Node)) {
        setShowHistory(false)
      }
      if (filterRef.current && !filterRef.current.contains(e.target as Node)) {
        setShowFilters(false)
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const handleSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (query.trim() && !isLoading) {
      addToHistory(query, filters)
      onSearch(query.trim(), filters)
      setShowHistory(false)
    }
  }

  const handleHistoryClick = (item: typeof history[0]) => {
    setQuery(item.query)
    if (item.filters) {
      // Validate fileType - ensure it's one of the allowed values
      const validFileTypes = ['all', 'images', 'videos']
      const fileType = validFileTypes.includes(item.filters.fileType || '')
        ? (item.filters.fileType as 'all' | 'images' | 'videos' | undefined)
        : undefined
      setFilters({
        ...item.filters,
        fileType,
      })
    }
    setShowHistory(false)
  }

  const suggestedQueries = suggestions && suggestions.length > 0
    ? suggestions
    : [
        'family time outdoors',
        'construction progress',
        'travel and exploration',
        'pets and animals',
        'events and celebrations',
      ]

  return (
    <div className="w-full space-y-4">
      {/* Main search input */}
      <form onSubmit={handleSubmit} className="w-full relative z-50">
        <div className="relative flex gap-2">
          <div className="flex-1 relative">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onFocus={() => history.length > 0 && setShowHistory(true)}
              placeholder={`Search by intent... e.g., '${suggestedQueries[0]}'`}
              disabled={isLoading}
              aria-label="Search query"
              className="w-full px-6 py-4 text-lg bg-gray-800 text-white border-2 border-gray-700 rounded-lg focus:outline-none focus:border-blue-500 transition disabled:opacity-50 disabled:cursor-not-allowed"
            />

            {/* History dropdown */}
            {showHistory && (
              <div
                ref={historyRef}
                className="absolute top-full left-0 right-0 mt-2 bg-gray-900 border border-gray-700 rounded-lg shadow-lg z-50 overflow-hidden"
              >
                {history.length > 0 ? (
                  <>
                    <div className="p-3 border-b border-gray-700">
                      <p className="text-xs text-gray-400 font-semibold uppercase tracking-wide">
                        Recent Searches
                      </p>
                    </div>
                    <div className="max-h-64 overflow-y-auto">
                      {history.map((item, idx) => (
                        <button
                          key={idx}
                          type="button"
                          onMouseDown={(e) => { e.preventDefault(); handleHistoryClick(item) }}
                          className="w-full text-left px-4 py-2 hover:bg-gray-800 transition text-sm text-gray-300 border-b border-gray-800 last:border-b-0"
                        >
                          <div className="flex justify-between items-start">
                            <span className="flex-1">{item.query}</span>
                            {item.filters?.fileType && item.filters.fileType !== 'all' && (
                              <span className="text-xs bg-blue-900 text-blue-200 px-2 py-1 rounded ml-2">
                                {item.filters.fileType}
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-gray-500 mt-1">
                            {new Date(item.timestamp).toLocaleDateString()}
                          </p>
                        </button>
                      ))}
                    </div>
                  </>
                ) : (
                  <div className="p-4 text-center text-sm text-gray-400">
                    No search history yet
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Filters toggle button */}
          <button
            type="button"
            onClick={() => setShowFilters(!showFilters)}
            aria-label="Toggle search filters"
            className="px-4 py-4 bg-gray-700 hover:bg-gray-600 text-white rounded-lg transition flex items-center gap-2"
          >
            <span>⚙️</span>
            <span className="hidden sm:inline text-sm font-semibold">Filters</span>
          </button>

          {/* Submit button */}
          <button
            type="submit"
            disabled={isLoading}
            aria-label={isLoading ? 'Searching...' : 'Search'}
            className="px-6 py-4 bg-gradient-to-r from-blue-500 to-purple-600 text-white font-semibold rounded-lg hover:from-blue-600 hover:to-purple-700 transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 whitespace-nowrap"
          >
            {isLoading ? (
              <>
                <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent"></div>
                <span className="hidden sm:inline">Searching</span>
              </>
            ) : (
              <>
                <span>🔍</span>
                <span className="hidden sm:inline">Search</span>
              </>
            )}
          </button>
        </div>
      </form>

      {/* Filters panel */}
      {showFilters && (
        <div
          ref={filterRef}
          className="bg-gray-800 border border-gray-700 rounded-lg p-6 space-y-6 animate-in fade-in slide-in-from-top-2"
        >
          <div className="flex justify-between items-center mb-4">
            <h3 className="text-lg font-semibold">Search Filters</h3>
            <button
              type="button"
              onClick={() => setShowFilters(false)}
              className="text-gray-400 hover:text-white transition"
            >
              ✕
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* File Type Filter */}
            <div>
              <label className="block text-sm font-semibold text-gray-300 mb-3">
                Media Type
              </label>
              <div className="space-y-2">
                {(['all', 'images', 'videos'] as const).map((type) => (
                  <label key={type} className="flex items-center gap-3 cursor-pointer">
                    <input
                      type="radio"
                      name="fileType"
                      value={type}
                      checked={filters.fileType === type}
                      onChange={(e) =>
                        setFilters({ ...filters, fileType: e.target.value as typeof type })
                      }
                      className="w-4 h-4 rounded"
                    />
                    <span className="text-sm text-gray-300 capitalize">
                      {type === 'all' ? 'All Media' : type}
                    </span>
                  </label>
                ))}
              </div>
            </div>

            {/* Date Range Filter */}
            <div>
              <label className="block text-sm font-semibold text-gray-300 mb-3">
                Date Range
              </label>
              <div className="space-y-2">
                <input
                  type="date"
                  value={filters.fromDate || ''}
                  onChange={(e) =>
                    setFilters({ ...filters, fromDate: e.target.value || undefined })
                  }
                  className="w-full px-3 py-2 bg-gray-700 text-white border border-gray-600 rounded text-sm focus:outline-none focus:border-blue-500"
                  aria-label="From date"
                />
                <input
                  type="date"
                  value={filters.toDate || ''}
                  onChange={(e) =>
                    setFilters({ ...filters, toDate: e.target.value || undefined })
                  }
                  className="w-full px-3 py-2 bg-gray-700 text-white border border-gray-600 rounded text-sm focus:outline-none focus:border-blue-500"
                  aria-label="To date"
                />
              </div>
            </div>
          </div>

          {/* Similarity Threshold */}
          <div>
            <div className="flex justify-between items-center mb-3">
              <label className="text-sm font-semibold text-gray-300">
                Minimum Similarity
              </label>
              <span className="text-sm text-blue-400 font-semibold">
                {Math.round((filters.minSimilarity ?? 0.3) * 100)}%
              </span>
            </div>
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={filters.minSimilarity ?? 0.3}
              onChange={(e) =>
                setFilters({ ...filters, minSimilarity: parseFloat(e.target.value) })
              }
              className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
              aria-label="Minimum similarity threshold"
            />
            <p className="text-xs text-gray-500 mt-2">
              Only show results matching {Math.round((filters.minSimilarity ?? 0.3) * 100)}% or higher
            </p>
          </div>

          {/* Results Limit */}
          <div>
            <div className="flex justify-between items-center mb-3">
              <label className="text-sm font-semibold text-gray-300">
                Max Results
              </label>
              <span className="text-sm text-blue-400 font-semibold">
                {filters.maxResults ?? 20}
              </span>
            </div>
            <div className="flex gap-2">
              {[20, 50, 100, 200].map((n) => (
                <button
                  key={n}
                  type="button"
                  onClick={() => setFilters({ ...filters, maxResults: n })}
                  className={`flex-1 py-2 rounded text-sm font-semibold transition ${
                    (filters.maxResults ?? 20) === n
                      ? 'bg-blue-600 text-white'
                      : 'bg-gray-700 text-gray-300 hover:bg-gray-600 hover:text-white'
                  }`}
                >
                  {n}
                </button>
              ))}
            </div>
            <p className="text-xs text-gray-500 mt-2">
              Larger values return more matches but take slightly longer
            </p>
          </div>

          {/* Scene Dedup Toggle */}
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-semibold text-gray-300">Scene Deduplication</p>
              <p className="text-xs text-gray-500 mt-0.5">
                Collapse near-identical frames from the same scene into one result
              </p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={filters.dedup ?? true}
              onClick={() => setFilters({ ...filters, dedup: !(filters.dedup ?? true) })}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                (filters.dedup ?? true) ? 'bg-blue-600' : 'bg-gray-600'
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  (filters.dedup ?? true) ? 'translate-x-6' : 'translate-x-1'
                }`}
              />
            </button>
          </div>

          {/* Reset Filters */}
          <button
            type="button"
            onClick={() =>
              setFilters({
                fileType: 'all',
                minSimilarity: 0.3,
                maxResults: 20,
                dedup: true,
              })
            }
            className="w-full px-4 py-2 bg-gray-700 hover:bg-gray-600 text-gray-300 hover:text-white rounded transition text-sm font-semibold"
          >
            Reset Filters
          </button>
        </div>
      )}

    </div>
  )
}
