/**
 * useVotes tests — clean architecture with separated concerns
 *
 * Structure:
 * 1. Pure logic tests (toggleVote, getVoteKey) — no React, no mocks
 * 2. Hook tests with injected API — isolated from network
 * 3. Integration tests — API contract verification
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useVotes, toggleVote, getVoteKey, type VoteAPI } from '@/hooks/useVotes'

// ============================================================================
// Pure Logic Tests — no React, no mocks needed
// ============================================================================

describe('getVoteKey', () => {
  it('returns file path alone when audioSegmentIndex is undefined', () => {
    expect(getVoteKey('/media/test.mp4')).toBe('/media/test.mp4')
    expect(getVoteKey('/media/test.mp4', undefined)).toBe('/media/test.mp4')
  })

  it('composes filePath#audioSegmentIndex when index is provided', () => {
    expect(getVoteKey('/media/test.mp4', 0)).toBe('/media/test.mp4#0')
    expect(getVoteKey('/media/test.mp4', 5)).toBe('/media/test.mp4#5')
    expect(getVoteKey('/path/with spaces/file.mp4', 99)).toBe('/path/with spaces/file.mp4#99')
  })
})

describe('toggleVote', () => {
  it('adds a new upvote to empty votes', () => {
    const { nextVotes, voteValue } = toggleVote({}, '/media/test.mp4#0', 1)
    expect(nextVotes).toEqual({ '/media/test.mp4#0': 1 })
    expect(voteValue).toBe(1)
  })

  it('adds a new downvote to empty votes', () => {
    const { nextVotes, voteValue } = toggleVote({}, '/media/test.mp4#0', -1)
    expect(nextVotes).toEqual({ '/media/test.mp4#0': -1 })
    expect(voteValue).toBe(-1)
  })

  it('clears vote when direction matches existing vote', () => {
    const votes: Record<string, 1 | -1> = { '/media/test.mp4#0': 1 }
    const { nextVotes, voteValue } = toggleVote(votes, '/media/test.mp4#0', 1)
    expect(nextVotes).toEqual({})
    expect(voteValue).toBe(0)
  })

  it('changes vote from upvote to downvote', () => {
    const votes: Record<string, 1 | -1> = { '/media/test.mp4#0': 1 }
    const { nextVotes, voteValue } = toggleVote(votes, '/media/test.mp4#0', -1)
    expect(nextVotes).toEqual({ '/media/test.mp4#0': -1 })
    expect(voteValue).toBe(-1)
  })

  it('does not mutate input votes object', () => {
    const votes: Record<string, 1 | -1> = { '/media/test.mp4#0': 1 }
    const original = JSON.stringify(votes)
    toggleVote(votes, '/media/test.mp4#0', 1)
    expect(JSON.stringify(votes)).toBe(original)
  })

  it('preserves unrelated votes', () => {
    const votes: Record<string, 1 | -1> = { '/media/test1.mp4#0': 1, '/media/test2.mp4#0': -1 }
    const { nextVotes } = toggleVote(votes, '/media/test1.mp4#0', 1)
    expect(nextVotes).toEqual({ '/media/test2.mp4#0': -1 })
  })
})

// ============================================================================
// Hook Tests — with injected mock API (isolated from network)
// ============================================================================

describe('useVotes hook', () => {
  let mockAPI: VoteAPI

  beforeEach(() => {
    // Create a mock API that tracks calls and resolves immediately
    mockAPI = {
      persist: vi.fn().mockResolvedValue(undefined),
    }
  })

  it('initializes with empty votes', () => {
    const { result } = renderHook(() => useVotes({ api: mockAPI }))
    expect(result.current.votes).toEqual({})
  })

  it('initializes with provided initialVotes', () => {
    const initialVotes: Record<string, 1 | -1> = { '/media/test.mp4#0': 1 }
    const { result } = renderHook(() => useVotes({ initialVotes, api: mockAPI }))
    expect(result.current.votes).toEqual(initialVotes)
  })

  it('updates votes optimistically when voting', async () => {
    const { result } = renderHook(() => useVotes({ api: mockAPI }))

    await act(async () => {
      result.current.vote('/media/test.mp4', 0, 1)
      // Don't wait for promise; state updates immediately
    })

    expect(result.current.votes).toEqual({ '/media/test.mp4#0': 1 })
  })

  it('calls API.persist with correct parameters when voting', async () => {
    const { result } = renderHook(() => useVotes({ api: mockAPI }))

    await act(async () => {
      await result.current.vote('/media/test.mp4', 5, 1)
    })

    expect(mockAPI.persist).toHaveBeenCalledWith('/media/test.mp4', 5, 1, undefined)
  })

  it('calls API.persist with 0 when clearing a vote', async () => {
    const initialVotes: Record<string, 1 | -1> = { '/media/test.mp4#0': 1 }
    const { result } = renderHook(() => useVotes({ initialVotes, api: mockAPI }))

    await act(async () => {
      await result.current.vote('/media/test.mp4', 0, 1)
    })

    expect(mockAPI.persist).toHaveBeenCalledWith('/media/test.mp4', 0, 0, undefined)
  })

  it('toggles vote: upvote → downvote', async () => {
    const initialVotes: Record<string, 1 | -1> = { '/media/test.mp4#0': 1 }
    const { result } = renderHook(() =>
      useVotes({ initialVotes, api: mockAPI })
    )

    await act(async () => {
      await result.current.vote('/media/test.mp4', 0, -1)
    })

    expect(result.current.votes).toEqual({ '/media/test.mp4#0': -1 })
    expect(mockAPI.persist).toHaveBeenCalledWith('/media/test.mp4', 0, -1, undefined)
  })

  it('merges server votes with optimistic votes when initialVotes prop changes', async () => {
    // The hook merges server state with local optimistic votes — it keeps any local keys
    // not yet confirmed by the server so SWR re-fetches don't wipe in-flight optimistic updates.
    const { result, rerender } = renderHook(
      ({ initialVotes }: { initialVotes?: Record<string, 1 | -1> }) =>
        useVotes({ initialVotes, api: mockAPI }),
      { initialProps: { initialVotes: { '/media/test1.mp4#0': 1 } } }
    )
    expect(result.current.votes).toEqual({ '/media/test1.mp4#0': 1 })

    await act(async () => {
      rerender({ initialVotes: { '/media/test2.mp4#0': -1 } })
    })

    // Server says test2=-1; test1 is kept as optimistic (server hasn't confirmed removal)
    expect(result.current.votes).toEqual({ '/media/test1.mp4#0': 1, '/media/test2.mp4#0': -1 })
  })

  it('handles API errors gracefully (logs but does not revert state)', async () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    mockAPI.persist = vi.fn().mockRejectedValue(new Error('Network error'))

    const { result } = renderHook(() => useVotes({ api: mockAPI }))

    await act(async () => {
      await result.current.vote('/media/test.mp4', 0, 1)
    })

    // State is still updated (optimistic)
    expect(result.current.votes).toEqual({ '/media/test.mp4#0': 1 })
    // Error is logged
    expect(consoleErrorSpy).toHaveBeenCalledWith(
      'Vote persistence failed:',
      expect.any(Error)
    )

    consoleErrorSpy.mockRestore()
  })

  it('tagVote upvotes with an explicit keyword instead of searchQuery', async () => {
    const { result } = renderHook(() => useVotes({ api: mockAPI }))

    await act(async () => {
      await result.current.tagVote('/media/test.mp4', 0, 'deep blowjob')
    })

    expect(result.current.votes).toEqual({ '/media/test.mp4#0': 1 })
    expect(mockAPI.persist).toHaveBeenCalledWith('/media/test.mp4', 0, 1, 'deep blowjob')
  })

  it('tagVote logs error but keeps optimistic state on failure', async () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    mockAPI.persist = vi.fn().mockRejectedValue(new Error('Tag vote failed'))

    const { result } = renderHook(() => useVotes({ api: mockAPI }))

    await act(async () => {
      await result.current.tagVote('/media/test.mp4', 0, 'yoga')
    })

    expect(result.current.votes).toEqual({ '/media/test.mp4#0': 1 })
    expect(consoleErrorSpy).toHaveBeenCalledWith('Tag vote persistence failed:', expect.any(Error))
    consoleErrorSpy.mockRestore()
  })

  it('allows multiple independent votes', async () => {
    const { result } = renderHook(() => useVotes({ api: mockAPI }))

    // Vote on first item
    await act(async () => {
      await result.current.vote('/media/test1.mp4', 0, 1)
    })
    expect(result.current.votes['/media/test1.mp4#0']).toBe(1)

    // Vote on second item (in separate act block to let state update)
    await act(async () => {
      await result.current.vote('/media/test2.mp4', 5, -1)
    })

    // Both votes should be present
    expect(result.current.votes).toEqual({
      '/media/test1.mp4#0': 1,
      '/media/test2.mp4#5': -1,
    })
  })
})
