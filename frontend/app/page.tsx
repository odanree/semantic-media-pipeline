'use client'

import { useState } from 'react'
import SearchBar from '@/components/SearchBar'
import ResultGrid from '@/components/ResultGrid'
import StatusPanel from '@/components/StatusPanel'

export default function SearchPage() {
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')

  const handleSearch = async (searchQuery: string) => {
    setQuery(searchQuery)
    setLoading(true)
    setError(null)

    try {
      const response = await fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query: searchQuery,
          limit: 20,
        }),
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

      <SearchBar onSearch={handleSearch} />

      {error && (
        <div className="mt-6 p-4 bg-red-900 border border-red-700 rounded-lg text-red-100">
          {error}
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
          <h3 className="text-xl font-semibold mb-4">
            Found {results.length} results for &quot;{query}&quot;
          </h3>
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
