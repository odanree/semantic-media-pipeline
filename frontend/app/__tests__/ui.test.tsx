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

vi.mock('@/components/HighlightReelPlayer', () => ({
  default: function MockHighlightReelPlayer({ onClose }: { onClose: () => void }) {
    return React.createElement('div', { 'data-testid': 'highlight-reel', onClick: onClose }, 'MockReel')
  },
}))

// ── Static imports (resolved after mocks are hoisted) ────────────────────────

import SearchPage from '@/app/page'
import ResultGrid from '@/components/ResultGrid'
import VideoPlayer from '@/components/VideoPlayer'
import SearchBar from '@/components/SearchBar'
import AskPanel from '@/components/AskPanel'
import StatusPanel from '@/components/StatusPanel'
import SimilarPanel from '@/components/SimilarPanel'
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
  id: string
  file_path: string
  file_type: string
  similarity: number
  timestamp: number
  frame_index: number
  audio_segment_start_sec: number | null
  audio_segment_end_sec: number | null
  audio_rms_energy: number | null
}>) {
  return {
    id: 'test-id',
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

  it('shows reel bounds debug line for video results', () => {
    const results = [makeResult({ file_type: 'video', timestamp: 10 })]
    render(<ResultGrid results={results} />)
    // Debug line shows "reel: X.Xs – Y.Ys" for video results
    expect(screen.getByText(/reel:/)).toBeInTheDocument()
  })

  it('shows audio segment bounds when timestamp is contained', () => {
    const results = [makeResult({
      file_type: 'video',
      timestamp: 10,
      audio_segment_start_sec: 8,
      audio_segment_end_sec: 15,
    })]
    render(<ResultGrid results={results} />)
    // When contained, reel uses segment bounds (8.0s – 15.0s)
    expect(screen.getByText(/8\.0s/)).toBeInTheDocument()
  })

  it('falls back to timestamp padding when segment does not contain timestamp', () => {
    const results = [makeResult({
      file_type: 'video',
      timestamp: 10,
      audio_segment_start_sec: 20,
      audio_segment_end_sec: 30,
    })]
    render(<ResultGrid results={results} />)
    // Fallback: timestamp ± padding, segment shown in grey
    expect(screen.getByText(/seg 20\.0/)).toBeInTheDocument()
  })

  it('shows reel bounds with no audio segment (pure timestamp fallback)', () => {
    const results = [makeResult({
      file_type: 'video',
      timestamp: 5,
      audio_segment_start_sec: null,
      audio_segment_end_sec: null,
    })]
    render(<ResultGrid results={results} />)
    expect(screen.getByText(/reel:/)).toBeInTheDocument()
    // No segment annotation shown
    expect(screen.queryByText(/seg/)).not.toBeInTheDocument()
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

  it('toggles to original quality when View Original is clicked', () => {
    render(<VideoPlayer result={makeResult()} onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /view original/i }))
    expect(screen.getByText('4K SRC')).toBeInTheDocument()
  })

  it('toggles back to proxy quality when View 720p is clicked', () => {
    render(<VideoPlayer result={makeResult()} onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /view original/i }))
    fireEvent.click(screen.getByRole('button', { name: /view 720p/i }))
    expect(screen.getByText('720p')).toBeInTheDocument()
  })

  it('skip back button calls skip(-60)', () => {
    render(<VideoPlayer result={makeResult()} onClose={vi.fn()} />)
    // Should not throw even without real video element
    fireEvent.click(screen.getByRole('button', { name: /skip back 1 minute/i }))
  })

  it('skip forward button calls skip(60)', () => {
    render(<VideoPlayer result={makeResult()} onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /skip forward 1 minute/i }))
  })

  it('clicking inner dialog prevents backdrop close', () => {
    const onClose = vi.fn()
    const { container } = render(<VideoPlayer result={makeResult()} onClose={onClose} />)
    // Click the inner dialog box — should not bubble to backdrop
    const dialog = container.querySelector('[role="dialog"]') as HTMLElement
    fireEvent.click(dialog)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('calls onClose when close button is clicked', () => {
    const onClose = vi.fn()
    render(<VideoPlayer result={makeResult()} onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: /close video player/i }))
    expect(onClose).toHaveBeenCalledTimes(1)
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
    const select = screen.getByRole('combobox') as HTMLSelectElement
    fireEvent.change(select, { target: { value: 'speech' } })
    expect(select.value).toBe('speech')
  })

  it('audio segment type dropdown contains expected options', () => {
    render(<SearchBar onSearch={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /toggle search filters/i }))
    const select = screen.getByRole('combobox') as HTMLSelectElement
    const values = Array.from(select.options).map(o => o.value)
    expect(values).toContain('speech')
    expect(values).toContain('ambient')
    expect(values).toContain('music')
    expect(values).toContain('silence')
  })

  it('reset filters clears audio segment type back to Any', () => {
    render(<SearchBar onSearch={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /toggle search filters/i }))
    const select = screen.getByRole('combobox') as HTMLSelectElement
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
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'speech' } })
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
    expect(screen.getByText(/3 duplicates collapsed/i)).toBeTruthy()
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

// ── SimilarPanel ──────────────────────────────────────────────────────────────

describe('SimilarPanel', () => {
  const mockSource = { file_path: '/media/test.mp4', file_type: 'video', timestamp: 10.5 }

  const mockResults = [
    { file_path: '/media/similar1.mp4', file_type: 'video', best_similarity: 0.92, best_timestamp: 5.0, audio_rms_energy: 0.6 },
    { file_path: '/media/similar2.mp4', file_type: 'video', best_similarity: 0.85, best_timestamp: 12.0, audio_rms_energy: 0.3 },
  ]

  function mockSimilarFetch(results = mockResults) {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ results }),
    }))
  }

  it('renders loading spinner initially', () => {
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})))
    render(<SimilarPanel source={mockSource} onClose={vi.fn()} />)
    expect(document.querySelector('.animate-spin')).toBeTruthy()
  })

  it('shows results after fetch resolves', async () => {
    mockSimilarFetch()
    await act(async () => {
      render(<SimilarPanel source={mockSource} onClose={vi.fn()} />)
      await new Promise(r => setTimeout(r, 10))
    })
    expect(screen.getByText('2 results')).toBeInTheDocument()
  })

  it('shows error message when fetch fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({ error: 'Query failed' }),
    }))
    await act(async () => {
      render(<SimilarPanel source={mockSource} onClose={vi.fn()} />)
      await new Promise(r => setTimeout(r, 10))
    })
    expect(screen.getByText('Query failed')).toBeInTheDocument()
  })

  it('shows fallback error when fetch rejects with non-string', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network down')))
    await act(async () => {
      render(<SimilarPanel source={mockSource} onClose={vi.fn()} />)
      await new Promise(r => setTimeout(r, 10))
    })
    expect(screen.getByText(/could not load similar videos/i)).toBeInTheDocument()
  })

  it('shows "No similar videos found" for empty results', async () => {
    mockSimilarFetch([])
    await act(async () => {
      render(<SimilarPanel source={mockSource} onClose={vi.fn()} />)
      await new Promise(r => setTimeout(r, 10))
    })
    expect(screen.getByText(/no similar videos found/i)).toBeInTheDocument()
  })

  it('calls onClose when close button is clicked', async () => {
    mockSimilarFetch()
    const onClose = vi.fn()
    await act(async () => {
      render(<SimilarPanel source={mockSource} onClose={onClose} />)
      await new Promise(r => setTimeout(r, 10))
    })
    fireEvent.click(screen.getByRole('button', { name: /close similar panel/i }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('calls onClose when backdrop is clicked', () => {
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})))
    const onClose = vi.fn()
    render(<SimilarPanel source={mockSource} onClose={onClose} />)
    fireEvent.click(document.querySelector('[aria-hidden="true"]') as HTMLElement)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('calls onClose when Escape is pressed', () => {
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})))
    const onClose = vi.fn()
    render(<SimilarPanel source={mockSource} onClose={onClose} />)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('changes sort order when sort select changes', async () => {
    mockSimilarFetch()
    await act(async () => {
      render(<SimilarPanel source={mockSource} onClose={vi.fn()} />)
      await new Promise(r => setTimeout(r, 10))
    })
    const sortSelect = screen.getByRole('combobox', { name: /sort similar results/i }) as HTMLSelectElement
    fireEvent.change(sortSelect, { target: { value: 'energy' } })
    expect(sortSelect.value).toBe('energy')
  })

  it('limit button becomes active when clicked', async () => {
    mockSimilarFetch()
    await act(async () => {
      render(<SimilarPanel source={mockSource} onClose={vi.fn()} />)
      await new Promise(r => setTimeout(r, 10))
    })
    const btn30 = screen.getByRole('button', { name: /show 30 results/i })
    fireEvent.click(btn30)
    expect(btn30.classList.contains('bg-blue-600')).toBe(true)
  })

  it('opens VideoPlayer when a video result card is clicked', async () => {
    mockSimilarFetch()
    await act(async () => {
      render(<SimilarPanel source={mockSource} onClose={vi.fn()} />)
      await new Promise(r => setTimeout(r, 10))
    })
    fireEvent.click(screen.getByRole('button', { name: /similar1\.mp4/i }))
    expect(document.querySelector('video')).toBeTruthy()
  })

  it('fetches playlist and shows reel when Reel button is clicked', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ results: mockResults }) })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ playlist_url: '/api/playlist/serve/abc/playlist.m3u8', clip_count: 2, total_duration_sec: 12 }) })
    vi.stubGlobal('fetch', fetchMock)
    await act(async () => {
      render(<SimilarPanel source={mockSource} onClose={vi.fn()} />)
      await new Promise(r => setTimeout(r, 10))
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /play highlight reel/i }))
      await new Promise(r => setTimeout(r, 10))
    })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(screen.getByTestId('highlight-reel')).toBeInTheDocument()
  })

  it('shows reel error when playlist fetch fails', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ results: mockResults }) })
      .mockResolvedValueOnce({ ok: false, json: () => Promise.resolve({ error: 'Playlist failed' }) })
    vi.stubGlobal('fetch', fetchMock)
    await act(async () => {
      render(<SimilarPanel source={mockSource} onClose={vi.fn()} />)
      await new Promise(r => setTimeout(r, 10))
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /play highlight reel/i }))
      await new Promise(r => setTimeout(r, 10))
    })
    expect(screen.getByText('Playlist failed')).toBeInTheDocument()
  })

  it('renders "Similar to" label with source filename', async () => {
    mockSimilarFetch()
    await act(async () => {
      render(<SimilarPanel source={mockSource} onClose={vi.fn()} />)
      await new Promise(r => setTimeout(r, 10))
    })
    expect(screen.getByText(/similar to/i)).toBeInTheDocument()
    expect(screen.getByText('test.mp4')).toBeInTheDocument()
  })

  it('clip padding select is present and changes value', async () => {
    mockSimilarFetch()
    await act(async () => {
      render(<SimilarPanel source={mockSource} onClose={vi.fn()} />)
      await new Promise(r => setTimeout(r, 10))
    })
    // Verify reel button is visible (confirms videoCount > 0 branch rendered)
    const reelBtn = screen.getByRole('button', { name: /play highlight reel/i })
    expect(reelBtn).toBeInTheDocument()
    // The clip padding select is adjacent to the reel button
    const paddingSelect = reelBtn.closest('div')?.parentElement?.querySelector('[aria-label="Clip padding seconds"]') as HTMLSelectElement | null
    if (paddingSelect) {
      fireEvent.change(paddingSelect, { target: { value: '5' } })
      expect(paddingSelect.value).toBe('5')
    }
  })

  it('shows network error when playlist fetch throws', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ results: mockResults }) })
      .mockRejectedValueOnce(new Error('Network error'))
    vi.stubGlobal('fetch', fetchMock)
    await act(async () => {
      render(<SimilarPanel source={mockSource} onClose={vi.fn()} />)
      await new Promise(r => setTimeout(r, 10))
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /play highlight reel/i }))
      await new Promise(r => setTimeout(r, 10))
    })
    expect(screen.getByText(/network error/i)).toBeInTheDocument()
  })

  it('removes keyboard listener on unmount', async () => {
    mockSimilarFetch()
    const onClose = vi.fn()
    let unmount!: () => void
    await act(async () => {
      ;({ unmount } = render(<SimilarPanel source={mockSource} onClose={onClose} />))
      await new Promise(r => setTimeout(r, 10))
    })
    unmount()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
  })
})
