'use client'

import { useState, useEffect, useMemo } from 'react'
import SearchBar, { SearchFilters } from '@/components/SearchBar'
import ResultGrid from '@/components/ResultGrid'
import StatusPanel from '@/components/StatusPanel'

interface CollectionInfo {
  total: number
  indexed: number
  percent_indexed: number
  by_type: Record<string, number>
  topic_tags: string[]
}

export default function SearchPage() {
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [appliedFilters, setAppliedFilters] = useState<SearchFilters>({})
  const [collectionInfo, setCollectionInfo] = useState<CollectionInfo | null>(null)

  useEffect(() => {
    fetch('/api/collection')
      .then(r => r.json())
      .then(setCollectionInfo)
      .catch(() => {}) // silently fall back to static text
  }, [])

  // Pick two example queries from the top topic tags
  const exampleQueries = useMemo(() => {
    const tags = collectionInfo?.topic_tags ?? []
    if (tags.length >= 2) return [tags[0], tags[1]]
    if (tags.length === 1) return [tags[0], 'outdoor activity']
    return ['person running on treadmill', 'yoga stretching']
  }, [collectionInfo])

  const handleSearch = async (searchQuery: string, filters: SearchFilters) => {
    setQuery(searchQuery)
    setAppliedFilters(filters)
    setLoading(true)
    setError(null)

    try {
      const payload: Record<string, unknown> = {
        query: searchQuery,
        limit: filters.maxResults ?? 20,
      }

      // Add filters to request if they differ from defaults
      if (filters.fileType && filters.fileType !== 'all') {
        payload.file_type = filters.fileType
      }
      if (filters.fromDate) {
        payload.from_date = filters.fromDate
      }
      if (filters.toDate) {
        payload.to_date = filters.toDate
      }
      if (filters.minSimilarity !== undefined && filters.minSimilarity !== 0.3) {
        payload.min_similarity = filters.minSimilarity
      }

      const response = await fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      if (!response.ok) {
        throw new Error('Search failed')
      }

      const data = await response.json()
      setResults(data.results || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
      setResults([])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <div className="mb-8">
        <h2 className="text-3xl font-bold mb-2">Find Media by Intent</h2>
        <p className="text-gray-400">
          Search your personal archive using natural language:
          <br />
          <em>
            &quot;{exampleQueries[0]}&quot; or &quot;{exampleQueries[1]}&quot;
          </em>
        </p>
        <div className="mt-4 flex flex-wrap gap-2 text-xs">
          <span className="bg-gray-800 border border-gray-700 text-gray-300 px-3 py-1 rounded-full">
            📂{' '}
            {collectionInfo
              ? `${collectionInfo.indexed.toLocaleString()} of ${collectionInfo.total.toLocaleString()} ${Object.keys(collectionInfo.by_type).map(t => `${t}s`).join('/')} indexed`
              : '~500 personal home videos'}
          </span>
        </div>
        {collectionInfo && collectionInfo.topic_tags.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
            <span className="text-gray-500">Try searching for:</span>
            {collectionInfo.topic_tags.map(tag => (
              <button
                key={tag}
                onClick={() => handleSearch(tag, {})}
                className="bg-blue-950 border border-blue-800 text-blue-300 px-3 py-1 rounded-full hover:bg-blue-900 transition-colors cursor-pointer"
              >
                {tag}
              </button>
            ))}
          </div>
        )}
      </div>

      <SearchBar onSearch={handleSearch} isLoading={loading} suggestions={collectionInfo?.topic_tags} />

      {error && (
        <div className="mt-6 p-4 bg-red-900 border border-red-700 rounded-lg text-red-100 flex justify-between items-center" role="alert">
          <span>{error}</span>
          <button
            onClick={() => setError(null)}
            className="text-red-200 hover:text-white text-sm font-semibold"
          >
            ✕ Dismiss
          </button>
        </div>
      )}

      {loading && (
        <div className="mt-6 text-center">
          <div className="inline-block">
            <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-blue-400"></div>
          </div>
          <p className="mt-4 text-gray-400">Searching...</p>
        </div>
      )}

      {!loading && results.length > 0 && (
        <div className="mt-8">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-4">
            <h3 className="text-xl font-semibold">
              Found {results.length} results for &quot;{query}&quot;
            </h3>
            {(appliedFilters.fileType !== 'all' ||
              appliedFilters.fromDate ||
              appliedFilters.toDate) && (
              <div className="flex flex-wrap gap-2">
                {appliedFilters.fileType && appliedFilters.fileType !== 'all' && (
                  <span className="text-xs bg-blue-900 text-blue-200 px-2 py-1 rounded">
                    {appliedFilters.fileType}
                  </span>
                )}
                {appliedFilters.fromDate && (
                  <span className="text-xs bg-green-900 text-green-200 px-2 py-1 rounded">
                    from {appliedFilters.fromDate}
                  </span>
                )}
                {appliedFilters.toDate && (
                  <span className="text-xs bg-purple-900 text-purple-200 px-2 py-1 rounded">
                    to {appliedFilters.toDate}
                  </span>
                )}
              </div>
            )}
          </div>
          <ResultGrid results={results} />
        </div>
      )}

      {!loading && query && results.length === 0 && !error && (
        <div className="mt-6 p-4 bg-gray-800 border border-gray-700 rounded-lg text-center text-gray-400">
          No results found for &quot;{query}&quot;. Try a different search query.
        </div>
      )}

      {!query && (
        <div className="mt-12">
          <StatusPanel />
        </div>
      )}
    </div>
  )
}
