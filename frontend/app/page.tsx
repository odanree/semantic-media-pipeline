'use client'

import { useState } from 'react'
import SearchBar, { SearchFilters } from '@/components/SearchBar'
import ResultGrid from '@/components/ResultGrid'
import StatusPanel from '@/components/StatusPanel'

export default function SearchPage() {
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [appliedFilters, setAppliedFilters] = useState<SearchFilters>({})

  const handleSearch = async (searchQuery: string, filters: SearchFilters) => {
    setQuery(searchQuery)
    setAppliedFilters(filters)
    setLoading(true)
    setError(null)

    try {
      const payload: any = {
        query: searchQuery,
        limit: 20,
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
            &quot;family trip to Vietnam in late 2025&quot; or &quot;home ADU construction&quot;
          </em>
        </p>
      </div>

      <SearchBar onSearch={handleSearch} isLoading={loading} />

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
