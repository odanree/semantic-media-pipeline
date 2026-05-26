import { useState, useEffect, useCallback } from 'react'
import { useSearchQuery } from '@/context/SearchContext'

// ============================================================================
// Pure Logic Layer — testable without React/mocks
// ============================================================================

/**
 * Compose a vote key from file_path and optional audio_segment_index.
 * Used as the key in the votes Record.
 */
export function getVoteKey(filePath: string, audioSegmentIndex?: number): string {
  if (audioSegmentIndex !== undefined && audioSegmentIndex !== null) {
    return `${filePath}#${audioSegmentIndex}`
  }
  return filePath
}

interface VoteToggleResult {
  nextVotes: Record<string, 1 | -1>
  voteValue: 0 | 1 | -1  // 0 = clear, 1 = upvote, -1 = downvote
}

/**
 * Pure vote toggle logic: given current votes and a direction, compute the next state.
 * - If already voted in that direction: clear (return vote=0)
 * - Otherwise: set to new direction (return vote=1 or -1)
 *
 * Testable without React, no side effects.
 */
export function toggleVote(
  votes: Record<string, 1 | -1>,
  key: string,
  direction: 1 | -1
): VoteToggleResult {
  if (votes[key] === direction) {
    // Clear vote
    const nextVotes = { ...votes }
    delete nextVotes[key]
    return { nextVotes, voteValue: 0 }
  }
  // Set new vote
  return { nextVotes: { ...votes, [key]: direction }, voteValue: direction }
}

// ============================================================================
// API Layer — mockable dependency
// ============================================================================

export interface VoteAPI {
  /**
   * Persist a vote change to the backend.
   * @param filePath - File path
   * @param audioSegmentIndex - Optional audio segment index
   * @param vote - 0 (clear), 1 (upvote), or -1 (downvote)
   * @param searchQuery - The query that produced this result (for vote_label indexing)
   * @throws on network or server error
   */
  persist(filePath: string, audioSegmentIndex: number | undefined, vote: 0 | 1 | -1, searchQuery?: string): Promise<void>
}

/**
 * Default API client using fetch.
 * Can be replaced with a mock in tests.
 */
function createDefaultVoteAPI(): VoteAPI {
  return {
    persist: async (filePath, audioSegmentIndex, vote, searchQuery) => {
      const response = await fetch('/api/vote', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_path: filePath, audio_segment_index: audioSegmentIndex, vote, search_query: searchQuery || null }),
      })
      if (!response.ok) {
        throw new Error(`Vote API error: ${response.status} ${response.statusText}`)
      }
    },
  }
}

// ============================================================================
// Hook — orchestrates state + API, injectable for testing
// ============================================================================

interface UseVotesOptions {
  initialVotes?: Record<string, 1 | -1>
  api?: VoteAPI  // Inject for testing; defaults to fetch-based implementation
}

export function useVotes(options: UseVotesOptions = {}) {
  const searchQuery = useSearchQuery()
  const [votes, setVotes] = useState<Record<string, 1 | -1>>(() => {
    return options.initialVotes ? { ...options.initialVotes } : {}
  })

  // Inject API: use provided mock or default to fetch
  const api = options.api || createDefaultVoteAPI()

  // Sync server votes when initialVotes changes (new search results / SWR re-fetch).
  // Merge rather than replace: keep optimistic votes that the server hasn't confirmed yet
  // so a SWR re-fetch mid-flight doesn't wipe an in-progress optimistic update.
  useEffect(() => {
    setVotes(prev => {
      const server = options.initialVotes ?? {}
      const merged: Record<string, 1 | -1> = { ...server }
      for (const [key, val] of Object.entries(prev)) {
        if (!(key in server)) {
          // Server doesn't know about this vote yet — keep the optimistic value
          merged[key] = val
        }
      }
      return merged
    })
  }, [options.initialVotes])

  const vote = useCallback(
    async (filePath: string, audioSegmentIndex: number | undefined, direction: 1 | -1) => {
      const key = getVoteKey(filePath, audioSegmentIndex)

      // Calculate next state synchronously using current votes
      const { nextVotes, voteValue } = toggleVote(votes, key, direction)

      // Optimistic update: update UI immediately
      setVotes(nextVotes)

      // Persist to backend asynchronously
      try {
        await api.persist(filePath, audioSegmentIndex, voteValue, searchQuery || undefined)
      } catch (err) {
        // Log error; UI stays updated (optimistic). Caller can handle via try/catch if needed.
        console.error('Vote persistence failed:', err)
        // Note: Could trigger a retry UI, notification, or rollback here if desired
      }
    },
    [votes, api, searchQuery]
  )

  // Tag with an explicit keyword — always upvotes, ignores current searchQuery.
  const tagVote = useCallback(
    async (filePath: string, audioSegmentIndex: number | undefined, keyword: string) => {
      const key = getVoteKey(filePath, audioSegmentIndex)
      setVotes(prev => ({ ...prev, [key]: 1 }))
      try {
        await api.persist(filePath, audioSegmentIndex, 1, keyword)
      } catch (err) {
        console.error('Tag vote persistence failed:', err)
      }
    },
    [api]
  )

  return { votes, vote, tagVote, getVoteKey }
}
