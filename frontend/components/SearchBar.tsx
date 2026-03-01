'use client'

import { FormEvent, useState } from 'react'

interface SearchBarProps {
  onSearch: (query: string) => void
  isLoading?: boolean
}

export default function SearchBar({ onSearch, isLoading = false }: SearchBarProps) {
  const [query, setQuery] = useState('')

  const handleSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (query.trim() && !isLoading) {
      onSearch(query.trim())
    }
  }

  return (
    <form onSubmit={handleSubmit} className="w-full">
      <div className="relative">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by intent... e.g., 'baby playing in the backyard' or 'construction progress'"
          disabled={isLoading}
          aria-label="Search query"
          className="w-full px-6 py-4 text-lg bg-gray-800 text-white border-2 border-gray-700 rounded-lg focus:outline-none focus:border-blue-500 transition disabled:opacity-50 disabled:cursor-not-allowed"
        />
        <button
          type="submit"
          disabled={isLoading}
          aria-label={isLoading ? 'Searching...' : 'Search'}
          className="absolute right-2 top-1/2 -translate-y-1/2 px-6 py-2 bg-gradient-to-r from-blue-500 to-purple-600 text-white font-semibold rounded-lg hover:from-blue-600 hover:to-purple-700 transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
        >
          {isLoading ? (
            <>
              <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent"></div>
              <span>Searching</span>
            </>
          ) : (
            'Search'
          )}
        </button>
      </div>
    </form>
  )
}
