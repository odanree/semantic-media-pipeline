'use client'

import { useEffect, useState } from 'react'

export interface SearchHistoryItem {
  query: string
  timestamp: number
  filters?: {
    fileType?: string
    fromDate?: string
    toDate?: string
    minSimilarity?: number
  }
}

export function useSearchHistory(maxItems: number = 10) {
  const [history, setHistory] = useState<SearchHistoryItem[]>([])
  const [isLoaded, setIsLoaded] = useState(false)

  const STORAGE_KEY = 'semantic-search-history'

  // Load history from localStorage on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY)
      if (stored) {
        const parsed = JSON.parse(stored) as SearchHistoryItem[]
        setHistory(parsed.slice(0, maxItems))
      }
    } catch (e) {
      console.error('Failed to load search history:', e)
    }
    setIsLoaded(true)
  }, [maxItems])

  const addToHistory = (query: string, filters?: SearchHistoryItem['filters']) => {
    if (!query.trim()) return

    const newItem: SearchHistoryItem = {
      query: query.trim(),
      timestamp: Date.now(),
      filters,
    }

    setHistory((prev) => {
      // Remove duplicate if it exists
      const filtered = prev.filter((item) => item.query !== query)
      const updated = [newItem, ...filtered].slice(0, maxItems)

      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(updated))
      } catch (e) {
        console.error('Failed to save search history:', e)
      }

      return updated
    })
  }

  const clearHistory = () => {
    setHistory([])
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch (e) {
      console.error('Failed to clear search history:', e)
    }
  }

  return {
    history,
    isLoaded,
    addToHistory,
    clearHistory,
  }
}
