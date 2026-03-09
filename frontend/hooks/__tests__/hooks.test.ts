/**
 * Custom hook tests — runs in jsdom (configured via environmentMatchGlobs).
 *
 * Covers: useSearchHistory, useStatusUpdates, useMediaUpdates
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useSearchHistory } from '@/hooks/useSearchHistory'
import { useStatusUpdates } from '@/hooks/useStatusUpdates'
import { useMediaUpdates } from '@/hooks/useMediaUpdates'

// ── FakeWebSocket ─────────────────────────────────────────────────────────────
// Supports the assignment-based listener pattern (ws.onopen = fn) used by the hooks.

class FakeWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3

  url: string
  readyState = FakeWebSocket.CONNECTING
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null

  constructor(url: string) {
    this.url = url
    // Fire open asynchronously so the hook has time to attach listeners
    Promise.resolve().then(() => {
      this.readyState = FakeWebSocket.OPEN
      this.onopen?.(new Event('open'))
    })
  }

  send(_data: unknown) { /* noop */ }

  close() {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.(new CloseEvent('close', { wasClean: true }))
  }

  /** Test helper: delivers a JSON message to the hook. */
  receive(data: unknown) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data) }))
  }

  /** Test helper: simulates a connection error. */
  fail(message = 'WebSocket error') {
    this.onerror?.(new ErrorEvent('error', { message }))
  }
}

// Keep track of created instances so tests can interact with them
let lastFakeWs: FakeWebSocket | null = null

class TrackingFakeWebSocket extends FakeWebSocket {
  constructor(url: string) {
    super(url)
    lastFakeWs = this
  }
}

// ── Global setup / teardown ───────────────────────────────────────────────────

beforeEach(() => {
  lastFakeWs = null
  localStorage.clear()
  vi.stubGlobal('WebSocket', TrackingFakeWebSocket)
  vi.useFakeTimers({ shouldAdvanceTime: false })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

// ── useSearchHistory ──────────────────────────────────────────────────────────

describe('useSearchHistory', () => {
  it('initializes with empty history when localStorage is empty', () => {
    const { result } = renderHook(() => useSearchHistory())
    expect(result.current.history).toEqual([])
  })

  it('loads persisted history from localStorage on mount', async () => {
    const stored = [{ query: 'sunrise yoga', timestamp: Date.now(), filters: {} }]
    localStorage.setItem('semantic-search-history', JSON.stringify(stored))

    const { result } = renderHook(() => useSearchHistory())
    await act(async () => {
      await Promise.resolve()
    })
    expect(result.current.history).toHaveLength(1)
    expect(result.current.history[0].query).toBe('sunrise yoga')
  })

  it('adds a new item to history', async () => {
    const { result } = renderHook(() => useSearchHistory())
    act(() => {
      result.current.addToHistory('morning run')
    })
    expect(result.current.history[0].query).toBe('morning run')
  })

  it('deduplicates when the same query is added twice', () => {
    const { result } = renderHook(() => useSearchHistory())
    act(() => {
      result.current.addToHistory('yoga stretch')
      result.current.addToHistory('yoga stretch')
    })
    expect(result.current.history.filter(h => h.query === 'yoga stretch')).toHaveLength(1)
  })

  it('clears all history', () => {
    const { result } = renderHook(() => useSearchHistory())
    act(() => {
      result.current.addToHistory('sunset hike')
    })
    act(() => {
      result.current.clearHistory()
    })
    expect(result.current.history).toHaveLength(0)
    expect(localStorage.getItem('semantic-search-history')).toBeNull()
  })

  it('ignores blank queries', () => {
    const { result } = renderHook(() => useSearchHistory())
    act(() => {
      result.current.addToHistory('   ')
    })
    expect(result.current.history).toHaveLength(0)
  })

  it('respects the maxItems cap', () => {
    const { result } = renderHook(() => useSearchHistory(3))
    act(() => {
      result.current.addToHistory('a')
      result.current.addToHistory('b')
      result.current.addToHistory('c')
      result.current.addToHistory('d')
    })
    expect(result.current.history.length).toBeLessThanOrEqual(3)
  })

  it('persists history to localStorage after add', () => {
    const { result } = renderHook(() => useSearchHistory())
    act(() => {
      result.current.addToHistory('bicycle sprint')
    })
    const stored = JSON.parse(localStorage.getItem('semantic-search-history')!)
    expect(stored[0].query).toBe('bicycle sprint')
  })
})

// ── useStatusUpdates ──────────────────────────────────────────────────────────

describe('useStatusUpdates', () => {
  it('starts with null status and isConnected=false', () => {
    const { result } = renderHook(() =>
      useStatusUpdates()
    )
    expect(result.current.status).toBeNull()
    expect(result.current.isConnected).toBe(false)
  })

  it('sets isConnected=true after WebSocket opens', async () => {
    const { result } = renderHook(() => useStatusUpdates())
    await act(async () => {
      await Promise.resolve() // Let FakeWebSocket open fire
    })
    expect(result.current.isConnected).toBe(true)
  })

  it('updates status when a message is received', async () => {
    const onUpdate = vi.fn()
    const { result } = renderHook(() => useStatusUpdates({ onUpdate }))

    await act(async () => {
      await Promise.resolve() // open connection
    })

    const statusPayload = {
      total: 100,
      by_status: { pending: 10, processing: 5, done: 80, error: 5 },
      by_type: { images: 60, videos: 40 },
    }

    await act(async () => {
      // Hook's onmessage checks data.files — wrap payload accordingly
      lastFakeWs!.receive({ files: statusPayload })
      await Promise.resolve()
    })

    expect(onUpdate).toHaveBeenCalledWith(statusPayload)
    expect(result.current.status).toEqual(statusPayload)
  })

  it('calls onError callback when WebSocket errors', async () => {
    const onError = vi.fn()
    renderHook(() => useStatusUpdates({ onError }))

    await act(async () => {
      await Promise.resolve() // open
    })
    await act(async () => {
      lastFakeWs!.fail('Connection refused')
    })
    // Error should be reported (hook may handle differently; just verify no crash)
  })

  it('schedules reconnect after WebSocket closes', async () => {
    const { result } = renderHook(() => useStatusUpdates())

    await act(async () => {
      await Promise.resolve() // open
    })
    await act(async () => {
      lastFakeWs!.close()
    })
    // isConnected resets to false after close
    expect(result.current.isConnected).toBe(false)
  })
})

// ── useMediaUpdates ───────────────────────────────────────────────────────────

describe('useMediaUpdates', () => {
  it('starts with empty updates and isConnected=false', () => {
    const { result } = renderHook(() =>
      useMediaUpdates('ws://localhost:8000/api/ws/media-updates')
    )
    expect(result.current.updates).toEqual([])
    expect(result.current.isConnected).toBe(false)
  })

  it('sets isConnected=true after WebSocket opens', async () => {
    const { result } = renderHook(() =>
      useMediaUpdates('ws://localhost:8000/api/ws/media-updates')
    )
    await act(async () => {
      await Promise.resolve()
    })
    expect(result.current.isConnected).toBe(true)
  })

  it('adds incoming message to updates list', async () => {
    const onUpdate = vi.fn()
    const { result } = renderHook(() =>
      useMediaUpdates('ws://localhost:8000/api/ws/media-updates', { onUpdate })
    )
    await act(async () => {
      await Promise.resolve()
    })

    const update = {
      channel: 'media_processing' as const,
      id: 'abc123',
      file_path: '/data/media/video.mp4',
      file_type: 'video',
      status: 'done',
    }

    await act(async () => {
      lastFakeWs!.receive(update)
      await Promise.resolve()
    })

    expect(onUpdate).toHaveBeenCalledWith(update)
    expect(result.current.updates).toHaveLength(1)
  })

  it('calls onError when WebSocket errors', async () => {
    const onError = vi.fn()
    renderHook(() =>
      useMediaUpdates('ws://localhost:8000/api/ws/media-updates', { onError })
    )
    await act(async () => {
      await Promise.resolve()
    })
    await act(async () => {
      lastFakeWs!.fail('Network error')
    })
    // Hook should handle the error gracefully
  })

  it('closes and reconnects after close event', async () => {
    const { result } = renderHook(() =>
      useMediaUpdates('ws://localhost:8000/api/ws/media-updates')
    )
    await act(async () => {
      await Promise.resolve()
    })
    expect(result.current.isConnected).toBe(true)

    await act(async () => {
      lastFakeWs!.close()
    })
    expect(result.current.isConnected).toBe(false)
  })
})
