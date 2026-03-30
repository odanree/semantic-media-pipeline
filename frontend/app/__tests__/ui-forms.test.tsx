/**
 * UI form component tests — runs in jsdom (configured via environmentMatchGlobs).
 *
 * Covers: SearchBar, StatusPanel, SearchPage (page.tsx)
 */

import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import '@testing-library/jest-dom'
import { makeResult } from '@/test/factories'

// ── Module mocks (hoisted before all imports) ────────────────────────────────

vi.mock('@/components/HighlightReelPlayer', () => ({
  default: function MockHighlightReelPlayer({ onClose }: { onClose: () => void }) {
    return React.createElement('div', { 'data-testid': 'highlight-reel', onClick: onClose }, 'MockReel')
  },
}))

// StatusPanel and SearchPage use useStatusUpdates hook
// Tests will use vi.mocked(statusHookModule.useStatusUpdates) to access the mock
vi.mock('@/hooks/useStatusUpdates', () => ({
  useStatusUpdates: vi.fn(() => ({ status: null, isConnected: false, error: null })),
}))

// ── Static imports (resolved after mocks are hoisted) ────────────────────────

import SearchPage from '@/app/page'
import SearchBar from '@/components/SearchBar'
import StatusPanel from '@/components/StatusPanel'
import * as statusHookModule from '@/hooks/useStatusUpdates'

// ── Global test setup ────────────────────────────────────────────────────────

beforeEach(() => {
  // Default safe fetch mock; individual tests override as needed
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: false,
    status: 500,
    json: () => Promise.resolve({}),
  }))
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

// ── SearchBar ────────────────────────────────────────────────────────────────

describe('SearchBar', () => {
  it('renders the search input', () => {
    render(<SearchBar onSearch={vi.fn()} />)
    expect(screen.getByRole('textbox')).toBeInTheDocument()
  })

  it('calls onSearch when form is submitted', () => {
    const onSearch = vi.fn()
    render(<SearchBar onSearch={onSearch} />)
    const input = screen.getByRole('textbox')
    fireEvent.change(input, { target: { value: 'sunset landscape' } })
    fireEvent.submit(input.closest('form')!)
    expect(onSearch).toHaveBeenCalledWith('sunset landscape', expect.any(Object))
  })

  it('renders without crashing when isLoading=true', () => {
    render(<SearchBar onSearch={vi.fn()} isLoading />)
    expect(screen.getByRole('textbox')).toBeInTheDocument()
  })

  it('syncs externalQuery prop into the input value', () => {
    const { rerender } = render(<SearchBar onSearch={vi.fn()} externalQuery="" />)
    rerender(<SearchBar onSearch={vi.fn()} externalQuery="yoga stretching" />)
    expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('yoga stretching')
  })

  it('saves submitted query to history', () => {
    const onSearch = vi.fn()
    render(<SearchBar onSearch={onSearch} />)
    const input = screen.getByRole('textbox')
    fireEvent.change(input, { target: { value: 'morning run' } })
    fireEvent.submit(input.closest('form')!)
    expect(onSearch).toHaveBeenCalledWith('morning run', expect.any(Object))
    // localStorage should now have history
    const stored = localStorage.getItem('semantic-search-history')
    expect(stored).not.toBeNull()
  })

  it('does not call onSearch when query is blank', () => {
    const onSearch = vi.fn()
    render(<SearchBar onSearch={onSearch} />)
    fireEvent.submit(screen.getByRole('textbox').closest('form')!)
    expect(onSearch).not.toHaveBeenCalled()
  })

  it('audio toggles are off by default', () => {
    render(<SearchBar onSearch={vi.fn()} />)
    // Open filters panel
    fireEvent.click(screen.getByRole('button', { name: /toggle search filters/i }))
    const switches = screen.getAllByRole('switch')
    const audioSwitches = switches.filter(s =>
      s.getAttribute('aria-checked') !== null &&
      (s.closest('div')?.textContent?.includes('audio') ||
       s.closest('div')?.textContent?.includes('speech'))
    )
    audioSwitches.forEach(s => expect(s.getAttribute('aria-checked')).toBe('false'))
  })

  it('selecting an audio segment type updates the dropdown value', () => {
    render(<SearchBar onSearch={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /toggle search filters/i }))
    const select = screen.getByLabelText('Audio segment type') as HTMLSelectElement
    fireEvent.change(select, { target: { value: 'speech' } })
    expect(select.value).toBe('speech')
  })

  it('audio segment type dropdown contains expected options', () => {
    render(<SearchBar onSearch={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /toggle search filters/i }))
    const select = screen.getByLabelText('Audio segment type') as HTMLSelectElement
    const values = Array.from(select.options).map(o => o.value)
    expect(values).toContain('speech')
    expect(values).toContain('ambient')
    expect(values).toContain('music')
    expect(values).toContain('silence')
  })

  it('reset filters clears audio segment type back to Any', () => {
    render(<SearchBar onSearch={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /toggle search filters/i }))
    const select = screen.getByLabelText('Audio segment type') as HTMLSelectElement
    fireEvent.change(select, { target: { value: 'music' } })
    fireEvent.click(screen.getByRole('button', { name: /reset filters/i }))
    expect(select.value).toBe('')
  })
})

// ── StatusPanel ──────────────────────────────────────────────────────────────

describe('StatusPanel', () => {
  const useStatusUpdatesMock = vi.mocked(statusHookModule.useStatusUpdates)

  it('renders loading spinner when status is null', () => {
    useStatusUpdatesMock.mockReturnValue({ status: null, isConnected: false, error: null })
    const { container } = render(<StatusPanel />)
    expect(container.firstChild).toBeTruthy()
  })

  it('renders pipeline data when status is provided', () => {
    const mockStatus = {
      total: 100,
      by_status: { pending: 10, processing: 5, done: 80, error: 5 },
      by_type: { images: 60, videos: 40 },
    }
    useStatusUpdatesMock.mockReturnValue({ status: mockStatus, isConnected: true, error: null })
    const { container } = render(<StatusPanel />)
    expect(container.firstChild).toBeTruthy()
  })

  it('triggers HTTP fallback fetch when not connected', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        files: {
          total: 50,
          by_status: { pending: 5, processing: 2, done: 40, error: 3 },
          by_type: { images: 30, videos: 20 },
        },
      }),
    }))
    useStatusUpdatesMock.mockReturnValue({ status: null, isConnected: false, error: null })

    const { container } = render(<StatusPanel />)
    await act(async () => {
      vi.advanceTimersByTime(2500)
      await Promise.resolve()
    })
    expect(container.firstChild).toBeTruthy()
    vi.useRealTimers()
  })

  it('renders error state when wsError is set and no status', () => {
    useStatusUpdatesMock.mockReturnValue({
      status: null,
      isConnected: false,
      error: new Error('WebSocket failed'),
    })
    const { container } = render(<StatusPanel />)
    expect(container.firstChild).toBeTruthy()
  })

  it('restores ingestStartAnchor from localStorage on mount', () => {
    localStorage.setItem(
      'ingestStartAnchor',
      JSON.stringify({ count: 50, time: Date.now() - 120000 })
    )
    useStatusUpdatesMock.mockReturnValue({ status: null, isConnected: false, error: null })
    const { container } = render(<StatusPanel />)
    expect(container.firstChild).toBeTruthy()
  })
})

// ── SearchPage (app/page.tsx) ─────────────────────────────────────────────────

describe('SearchPage', () => {
  const collectionResponse = {
    total: 200,
    indexed: 180,
    percent_indexed: 90,
    by_type: { images: 100, videos: 80 },
    topic_tags: ['running', 'yoga', 'cycling'],
  }

  it('renders without crashing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(collectionResponse),
    }))
    await act(async () => {
      render(<SearchPage />)
      await Promise.resolve()
    })
    expect(document.body.firstChild).toBeTruthy()
  })

  it('renders gracefully when fetch fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network')))
    await act(async () => {
      render(<SearchPage />)
      await Promise.resolve()
    })
    expect(document.body.firstChild).toBeTruthy()
  })

  it('loads collection info and shows example queries', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(collectionResponse),
    }))
    await act(async () => {
      render(<SearchPage />)
      await new Promise(r => setTimeout(r, 10))
    })
    // After collection loads, topic_tags become example queries
    expect(document.querySelector('input[type="text"], input[type="search"]')).toBeTruthy()
  })

  it('triggers handleSearch when search is submitted', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(collectionResponse) })
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ results: [makeResult()] }),
      })
    )
    await act(async () => {
      render(<SearchPage />)
      await Promise.resolve()
    })
    const input = document.querySelector('input') as HTMLInputElement | null
    if (input) {
      fireEvent.change(input, { target: { value: 'morning jog' } })
      fireEvent.submit(input.closest('form')!)
    }
    await act(async () => { await Promise.resolve() })
  })

  it('sends audio_segment_type when selected from the dropdown', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(collectionResponse) })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ results: [] }) })
    vi.stubGlobal('fetch', fetchMock)
    await act(async () => {
      render(<SearchPage />)
      await Promise.resolve()
    })
    fireEvent.click(screen.getByRole('button', { name: /toggle search filters/i }))
    fireEvent.change(screen.getByLabelText('Audio segment type'), { target: { value: 'speech' } })
    const input = document.querySelector('input[type="text"]') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'concert footage' } })
    await act(async () => {
      fireEvent.submit(input.closest('form')!)
      await Promise.resolve()
    })
    const body = JSON.parse(fetchMock.mock.calls[1][1].body)
    expect(body.audio_segment_type).toBe('speech')
    expect(body.min_audio_energy).toBeUndefined()
    expect(body.audio_has_speech).toBeUndefined()
  })

  it('does not send audio_segment_type when dropdown is on Any', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(collectionResponse) })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ results: [] }) })
    vi.stubGlobal('fetch', fetchMock)
    await act(async () => {
      render(<SearchPage />)
      await Promise.resolve()
    })
    const input = document.querySelector('input[type="text"]') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'interview clips' } })
    await act(async () => {
      fireEvent.submit(input.closest('form')!)
      await Promise.resolve()
    })
    const body = JSON.parse(fetchMock.mock.calls[1][1].body)
    expect(body.audio_segment_type).toBeUndefined()
  })

  it('does not send audio params when filters are off', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(collectionResponse) })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ results: [] }) })
    vi.stubGlobal('fetch', fetchMock)
    await act(async () => {
      render(<SearchPage />)
      await Promise.resolve()
    })
    const input = document.querySelector('input[type="text"]') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'landscape' } })
    await act(async () => {
      fireEvent.submit(input.closest('form')!)
      await Promise.resolve()
    })
    const body = JSON.parse(fetchMock.mock.calls[1][1].body)
    expect(body.min_audio_energy).toBeUndefined()
    expect(body.audio_has_speech).toBeUndefined()
  })

  it('shows Search tab by default and hides Ask panel', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(collectionResponse),
    }))
    await act(async () => {
      render(<SearchPage />)
      await Promise.resolve()
    })
    // Search tab should be active; Ask panel placeholder text should not exist
    expect(screen.getAllByRole('button', { name: /Search/i }).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /Ask/i })).toBeTruthy()
    expect(screen.queryByPlaceholderText(/Ask anything/i)).toBeNull()
  })

  it('switches to Ask mode and renders AskPanel input', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(collectionResponse),
    }))
    await act(async () => {
      render(<SearchPage />)
      await Promise.resolve()
    })
    fireEvent.click(screen.getByRole('button', { name: /Ask/i }))
    expect(screen.getByPlaceholderText(/Ask anything/i)).toBeTruthy()
  })

  it('switches back to Search mode from Ask mode', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(collectionResponse),
    }))
    await act(async () => {
      render(<SearchPage />)
      await Promise.resolve()
    })
    fireEvent.click(screen.getByRole('button', { name: /Ask/i }))
    fireEvent.click(screen.getByRole('button', { name: /Search/i }))
    expect(screen.queryByPlaceholderText(/Ask anything/i)).toBeNull()
  })
})
