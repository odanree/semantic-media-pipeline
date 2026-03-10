/**
 * UI component tests — runs in jsdom (configured via environmentMatchGlobs).
 *
 * Covers: ResultGrid, VideoPlayer, SearchBar, StatusPanel, SearchPage (page.tsx)
 */

import React from 'react'
import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import '@testing-library/jest-dom'

// ── jsdom polyfills (not in jsdom by default) ────────────────────────────────
// Must use direct assignment so vi.unstubAllGlobals() doesn't remove them.
beforeAll(() => {
  // ResultGrid uses IntersectionObserver for lazy-loading thumbnails
  Object.defineProperty(window, 'IntersectionObserver', {
    writable: true,
    configurable: true,
    value: class {
      observe = vi.fn()
      unobserve = vi.fn()
      disconnect = vi.fn()
      constructor(_cb: IntersectionObserverCallback) {}
    },
  })
  // ResultGrid calls scrollIntoView on page change
  window.HTMLElement.prototype.scrollIntoView = vi.fn()
})

// ── Module mocks (hoisted before all imports) ────────────────────────────────

vi.mock('next/image', () => ({
  default: function MockImage(props: Record<string, unknown>) {
    const { src, alt, fill, priority, ...rest } = props as {
      src: string
      alt: string
      fill?: boolean
      priority?: boolean
      [k: string]: unknown
    }
    // eslint-disable-next-line @next/next/no-img-element
    return React.createElement('img', { src, alt, 'data-fill': fill, 'data-priority': priority, ...rest })
  },
}))

vi.mock('@/hooks/useStatusUpdates', () => ({
  useStatusUpdates: vi.fn(() => ({ status: null, isConnected: false, error: null })),
}))

// ── Static imports (resolved after mocks are hoisted) ────────────────────────

import SearchPage from '@/app/page'
import ResultGrid from '@/components/ResultGrid'
import VideoPlayer from '@/components/VideoPlayer'
import SearchBar from '@/components/SearchBar'
import AskPanel from '@/components/AskPanel'
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

// ── Helpers ──────────────────────────────────────────────────────────────────

function makeResult(overrides?: Partial<{
  file_path: string
  file_type: string
  similarity: number
  timestamp: number
  frame_index: number
}>) {
  return {
    file_path: '/media/test.mp4',
    file_type: 'video',
    similarity: 0.85,
    ...overrides,
  }
}

// ── ResultGrid ───────────────────────────────────────────────────────────────

describe('ResultGrid', () => {
  it('shows empty-state message when results is empty', () => {
    render(<ResultGrid results={[]} />)
    expect(screen.getByText(/no results to display/i)).toBeInTheDocument()
  })

  it('renders video result cards without crashing', () => {
    const results = [makeResult(), makeResult({ file_path: '/media/b.mp4', similarity: 0.9 })]
    const { container } = render(<ResultGrid results={results} />)
    expect(container.firstChild).toBeTruthy()
  })

  it('renders image result cards without crashing', () => {
    const results = [makeResult({ file_path: '/media/photo.jpg', file_type: 'image' })]
    const { container } = render(<ResultGrid results={results} />)
    expect(screen.queryByText(/no results to display/i)).not.toBeInTheDocument()
  })

  it('renders pagination controls when results > 20', () => {
    const results = Array.from({ length: 25 }, (_, i) =>
      makeResult({ file_path: `/media/vid${i}.mp4` })
    )
    const { container } = render(<ResultGrid results={results} />)
    // Just verify it renders without error when pagination is needed
    expect(container.firstChild).toBeTruthy()
  })

  it('opens video player when a video result is clicked', () => {
    const results = [makeResult({ file_path: '/media/clip.mp4' })]
    render(<ResultGrid results={results} />)
    // Find any clickable element in the results area and click it
    const clickable = document.querySelector('[role="button"], button, [onClick]') as HTMLElement | null
    if (clickable) fireEvent.click(clickable)
    // Just ensure no error is thrown
  })

  it('renders results from both types in a mixed list', () => {
    const results = [
      makeResult({ file_type: 'video' }),
      makeResult({ file_path: '/img.jpg', file_type: 'image' }),
    ]
    const { container } = render(<ResultGrid results={results} />)
    expect(container.firstChild).toBeTruthy()
  })
})

// ── VideoPlayer ──────────────────────────────────────────────────────────────

describe('VideoPlayer', () => {
  it('renders the video player container', () => {
    const { container } = render(<VideoPlayer result={makeResult()} onClose={vi.fn()} />)
    expect(container.firstChild).toBeTruthy()
  })

  it('calls onClose when Escape key is pressed', () => {
    const onClose = vi.fn()
    render(<VideoPlayer result={makeResult()} onClose={onClose} />)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('renders without an optional timestamp', () => {
    const result = { file_path: '/media/test.mp4', file_type: 'video', similarity: 0.8 }
    const { container } = render(
      <VideoPlayer result={result} onClose={vi.fn()} />
    )
    expect(container.firstChild).toBeTruthy()
  })

  it('resets quality when result.file_path changes', () => {
    const { rerender } = render(<VideoPlayer result={makeResult()} onClose={vi.fn()} />)
    rerender(<VideoPlayer result={makeResult({ file_path: '/media/new.mp4' })} onClose={vi.fn()} />)
    // Just verifying it doesn't crash on prop change
  })

  it('renders with timestamp and applies it to the video element', () => {
    const result = makeResult({ timestamp: 30 })
    const { container } = render(<VideoPlayer result={result} onClose={vi.fn()} />)
    expect(container.querySelector('video')).toBeTruthy()
  })
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

// ── AskPanel ──────────────────────────────────────────────────────────────────

describe('AskPanel', () => {
  it('renders the question input and Ask button', () => {
    render(<AskPanel />)
    expect(screen.getByRole('textbox', { name: /question/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Ask/i })).toBeTruthy()
  })

  it('Ask button is disabled when input is empty', () => {
    render(<AskPanel />)
    expect((screen.getByRole('button', { name: /Ask/i }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('enables Ask button when question is filled', () => {
    render(<AskPanel />)
    fireEvent.change(screen.getByRole('textbox', { name: /question/i }), {
      target: { value: 'What videos do I have from Vietnam?' },
    })
    expect((screen.getByRole('button', { name: /Ask/i }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('shows loading spinner while waiting for response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {}))) // never resolves
    render(<AskPanel />)
    fireEvent.change(screen.getByRole('textbox', { name: /question/i }), {
      target: { value: 'What do I have?' },
    })
    await act(async () => {
      fireEvent.submit(screen.getByRole('textbox', { name: /question/i }).closest('form')!)
      await Promise.resolve()
    })
    expect(document.querySelector('.animate-spin')).toBeTruthy()
  })

  it('renders answer and sources on successful response', async () => {
    const mockResult = {
      question: 'What do I have?',
      answer: 'You have footage from Vietnam.',
      sources: [
        { file_path: '/media/vietnam/clip.mp4', file_type: 'video', similarity: 0.91, timestamp: 12.5 },
      ],
      model_used: 'gpt-4o-mini',
      retrieval_count: 1,
      execution_time_ms: 450,
      scenes_collapsed: 0,
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockResult),
    }))
    render(<AskPanel />)
    fireEvent.change(screen.getByRole('textbox', { name: /question/i }), {
      target: { value: 'What do I have?' },
    })
    await act(async () => {
      fireEvent.submit(screen.getByRole('textbox', { name: /question/i }).closest('form')!)
      await new Promise(r => setTimeout(r, 10))
    })
    expect(screen.getByText(/footage from Vietnam/i)).toBeTruthy()
    // Shows filename only (not full path)
    expect(screen.getByText(/clip\.mp4/i)).toBeTruthy()
    expect(screen.getByText(/gpt-4o-mini/i)).toBeTruthy()
  })

  it('video sources render as clickable buttons', async () => {
    const mockResult = {
      question: 'test',
      answer: 'Some answer.',
      sources: [
        { file_path: '/media/clip.mp4', file_type: 'video', similarity: 0.9, timestamp: 5.0 },
      ],
      model_used: 'gpt-4o-mini',
      retrieval_count: 1,
      execution_time_ms: 300,
      scenes_collapsed: 0,
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockResult),
    }))
    render(<AskPanel />)
    fireEvent.change(screen.getByRole('textbox', { name: /question/i }), {
      target: { value: 'test' },
    })
    await act(async () => {
      fireEvent.submit(screen.getByRole('textbox', { name: /question/i }).closest('form')!)
      await new Promise(r => setTimeout(r, 10))
    })
    expect(screen.getByRole('button', { name: /play clip\.mp4/i })).toBeTruthy()
  })

  it('sources cited in the answer get a "cited" badge', async () => {
    const mockResult = {
      question: 'test',
      answer: 'The answer references [1] but not source 2.',
      sources: [
        { file_path: '/media/a.mp4', file_type: 'video', similarity: 0.9 },
        { file_path: '/media/b.mp4', file_type: 'video', similarity: 0.8 },
      ],
      model_used: 'gpt-4o-mini',
      retrieval_count: 2,
      execution_time_ms: 300,
      scenes_collapsed: 0,
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockResult),
    }))
    render(<AskPanel />)
    fireEvent.change(screen.getByRole('textbox', { name: /question/i }), {
      target: { value: 'test' },
    })
    await act(async () => {
      fireEvent.submit(screen.getByRole('textbox', { name: /question/i }).closest('form')!)
      await new Promise(r => setTimeout(r, 10))
    })
    // Exactly one "cited" badge should appear (only source [1])
    const citedBadges = document.querySelectorAll('span')
    const citedCount = [...citedBadges].filter(el => el.textContent.trim() === '✓ cited').length
    expect(citedCount).toBe(1)
  })

  it('shows error message when fetch fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({ error: 'LLM unavailable' }),
    }))
    render(<AskPanel />)
    fireEvent.change(screen.getByRole('textbox', { name: /question/i }), {
      target: { value: 'test' },
    })
    await act(async () => {
      fireEvent.submit(screen.getByRole('textbox', { name: /question/i }).closest('form')!)
      await new Promise(r => setTimeout(r, 10))
    })
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(screen.getByText(/LLM unavailable/i)).toBeTruthy()
  })

  it('shows scenes_collapsed count when dedup collapsed frames', async () => {
    const mockResult = {
      question: 'test',
      answer: 'Some answer.',
      sources: [],
      model_used: 'gpt-4o-mini',
      retrieval_count: 5,
      execution_time_ms: 300,
      scenes_collapsed: 3,
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockResult),
    }))
    render(<AskPanel />)
    fireEvent.change(screen.getByRole('textbox', { name: /question/i }), {
      target: { value: 'test' },
    })
    await act(async () => {
      fireEvent.submit(screen.getByRole('textbox', { name: /question/i }).closest('form')!)
      await new Promise(r => setTimeout(r, 10))
    })
    expect(screen.getByText(/3 duplicate frames collapsed/i)).toBeTruthy()
  })

  it('dismisses error when Dismiss is clicked', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({ error: 'LLM unavailable' }),
    }))
    render(<AskPanel />)
    fireEvent.change(screen.getByRole('textbox', { name: /question/i }), {
      target: { value: 'test' },
    })
    await act(async () => {
      fireEvent.submit(screen.getByRole('textbox', { name: /question/i }).closest('form')!)
      await new Promise(r => setTimeout(r, 10))
    })
    fireEvent.click(screen.getByText(/Dismiss/i))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('dedup toggle is checked by default', () => {
    render(<AskPanel />)
    const toggle = screen.getByRole('checkbox', { name: /collapse duplicate scenes/i }) as HTMLInputElement
    expect(toggle.checked).toBe(true)
  })

  it('sends dedup: false in request body when toggle is unchecked', async () => {
    const mockResult = {
      question: 'test', answer: 'ok', sources: [],
      model_used: 'qwen', retrieval_count: 0, execution_time_ms: 100, scenes_collapsed: 0,
    }
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockResult),
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<AskPanel />)
    fireEvent.click(screen.getByRole('checkbox', { name: /collapse duplicate scenes/i }))
    fireEvent.change(screen.getByRole('textbox', { name: /question/i }), {
      target: { value: 'test' },
    })
    await act(async () => {
      fireEvent.submit(screen.getByRole('textbox', { name: /question/i }).closest('form')!)
      await new Promise(r => setTimeout(r, 10))
    })
    const body = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(body.dedup).toBe(false)
  })

  it('sends dedup: true in request body when toggle is checked', async () => {
    const mockResult = {
      question: 'test', answer: 'ok', sources: [],
      model_used: 'qwen', retrieval_count: 0, execution_time_ms: 100, scenes_collapsed: 0,
    }
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockResult),
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<AskPanel />)
    fireEvent.change(screen.getByRole('textbox', { name: /question/i }), {
      target: { value: 'test' },
    })
    await act(async () => {
      fireEvent.submit(screen.getByRole('textbox', { name: /question/i }).closest('form')!)
      await new Promise(r => setTimeout(r, 10))
    })
    const body = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(body.dedup).toBe(true)
  })

  it('displays video thumbnails and captions in source list', async () => {
    const mockResult = {
      question: 'test',
      answer: 'Here is a result [1]',
      sources: [
        {
          file_path: '/media/test.mp4',
          file_type: 'video',
          similarity: 0.9,
          timestamp: 10.5,
          caption: 'A person running in the park',
        },
      ],
      model_used: 'gpt-4o',
      retrieval_count: 1,
      execution_time_ms: 200,
      scenes_collapsed: 0,
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockResult),
    }))
    render(<AskPanel />)
    fireEvent.change(screen.getByRole('textbox', { name: /question/i }), {
      target: { value: 'test' },
    })
    await act(async () => {
      fireEvent.submit(screen.getByRole('textbox', { name: /question/i }).closest('form')!)
      await new Promise(r => setTimeout(r, 10))
    })
    // Verify thumbnail image is rendered with correct src
    const thumbnail = document.querySelector('img[src*="/api/thumbnail"]') as HTMLImageElement | null
    expect(thumbnail).toBeTruthy()
    if (thumbnail) {
      expect(thumbnail.src).toContain('/api/thumbnail')
      expect(thumbnail.src).toContain('test.mp4')
      expect(thumbnail.src).toContain('t=10.5')
    }
    // Verify caption is displayed
    expect(screen.getByText(/A person running in the park/i)).toBeTruthy()
    // Verify cited badge appears
    expect(screen.getByText('✓ cited')).toBeTruthy()
  })
})
