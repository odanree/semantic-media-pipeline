'use client'

import { createContext, useContext } from 'react'

const SearchContext = createContext<string>('')

export const SearchProvider = SearchContext.Provider

export function useSearchQuery(): string {
  return useContext(SearchContext)
}
