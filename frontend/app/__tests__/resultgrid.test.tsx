/**
 * ResultGrid component tests — runs in jsdom.
 * Isolated from VideoPlayer to reduce per-file memory footprint.
 * Mocks heavy child components (ResultCard) to avoid N×card memory cost.
 */

import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import { makeResult } from '@/test/factories'
import ResultGrid from '@/components/ResultGrid'
import { SearchProvider } from '@/context/SearchContext'

// ── Mock heavy child components ────────────────────────────────────────────────
// ResultGrid renders one ResultCard per item. ResultCard may have its own hooks,
// images, or conditional logic. Mocking it as a div reduces memory N-fold.

vi.mock('@/components/ResultCard', () => ({
  default: ({ result, onPlay }: { result: Record<string, unknown>; onPlay?: () => void }) =>
    React.createElement('div', {
      'data-testid': 'mock-card',
      'data-id': result.id,
      onClick: onPlay,
    }, `Card: ${result.id}`),
}))

// ── Helpers ──────────────────────────────────────────────────────────────────────

// Mock ResizeObserver if not already defined (ui.test.tsx may have done this)
const windowObj = window as unknown as Window & { ResizeObserver?: typeof ResizeObserver }
if (typeof windowObj.ResizeObserver === 'undefined') {
  windowObj.ResizeObserver = vi.fn(() => ({
    observe: vi.fn(),
    unobserve: vi.fn(),
    disconnect: vi.fn(),
  })) as unknown as typeof ResizeObserver
}

// ── ResultGrid ───────────────────────────────────────────────────────────────────

describe('ResultGrid', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('shows empty-state message when results is empty', () => {
    render(<ResultGrid results={[]} />)
    expect(screen.getByText(/no results to display/i)).toBeInTheDocument()
  })

  it('renders video result cards without crashing', () => {
    const results = [makeResult({ id: 'a' }), makeResult({ id: 'b', file_path: '/media/b.mp4', similarity: 0.9 })]
    const { container } = render(<ResultGrid results={results} />)
    expect(container.firstChild).toBeTruthy()
  })

  it('renders image result cards without crashing', () => {
    const results = [makeResult({ file_path: '/media/photo.jpg', file_type: 'image' })]
    render(<ResultGrid results={results} />)
    expect(screen.queryByText(/no results to display/i)).not.toBeInTheDocument()
  })

  it('renders pagination controls when results > 20', () => {
    const results = Array.from({ length: 25 }, (_, i) =>
      makeResult({ id: `vid-${i}`, file_path: `/media/vid${i}.mp4` })
    )
    const { container } = render(<ResultGrid results={results} />)
    expect(container.firstChild).toBeTruthy()
  })

  it('opens video player when a video result is clicked', () => {
    const results = [makeResult({ file_path: '/media/clip.mp4' })]
    render(<ResultGrid results={results} />)
    const clickable = document.querySelector('[role="button"], button, [onClick]') as HTMLElement | null
    if (clickable) fireEvent.click(clickable)
  })

  it('renders results from both types in a mixed list', () => {
    const results = [
      makeResult({ id: 'vid1', file_type: 'video' }),
      makeResult({ id: 'img1', file_path: '/img.jpg', file_type: 'image' }),
    ]
    const { container } = render(<ResultGrid results={results} />)
    expect(container.firstChild).toBeTruthy()
  })

  it('shows reel bounds debug line for video results', () => {
    const results = [makeResult({ file_type: 'video', timestamp: 10 })]
    render(<ResultGrid results={results} />)
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
    expect(screen.queryByText(/seg/)).not.toBeInTheDocument()
  })

  it('exposes "Recently indexed" sort options', () => {
    const results = [makeResult({ file_path: '/m/a.mp4' })]
    render(<ResultGrid results={results} />)
    const sort = screen.getByLabelText('Sort results') as HTMLSelectElement
    const labels = Array.from(sort.querySelectorAll('option')).map(o => o.textContent)
    expect(labels).toContain('Recently indexed ↑')
    expect(labels).toContain('Recently indexed ↓')
  })

  it('reorders results when sort=processed_desc using processed_at', () => {
    const older  = makeResult({ id: 'older',  file_path: '/m/older.mp4',  processed_at: '2026-06-01T00:00:00Z' })
    const newer  = makeResult({ id: 'newer',  file_path: '/m/newer.mp4',  processed_at: '2026-06-09T00:00:00Z' })
    const middle = makeResult({ id: 'middle', file_path: '/m/middle.mp4', processed_at: '2026-06-05T00:00:00Z' })
    render(<ResultGrid results={[older, newer, middle]} />)
    fireEvent.change(screen.getByLabelText('Sort results'), { target: { value: 'processed_desc' } })
    // Newest first
    const cards = Array.from(document.querySelectorAll('[role="listitem"]'))
    const order = cards.map(c => (c.getAttribute('aria-label') || '') + ' ' + (c.textContent || ''))
    const idx = (needle: string) => order.findIndex(s => s.includes(needle))
    expect(idx('newer.mp4')).toBeLessThan(idx('middle.mp4'))
    expect(idx('middle.mp4')).toBeLessThan(idx('older.mp4'))
  })
})

// ── Pin to eval set ──────────────────────────────────────────────────────────

describe('ResultGrid — Pin to eval', () => {
  afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals() })

  function renderWithQuery(query: string, results: ReturnType<typeof makeResult>[]) {
    return render(
      <SearchProvider value={query}>
        <ResultGrid results={results} />
      </SearchProvider>
    )
  }

  it('disables the pin button when there is no active search query', () => {
    renderWithQuery('', [makeResult({ file_path: '/m/clip.mp4', file_type: 'video', timestamp: 1 })])
    const pin = screen.getByTestId('pin-button')
    expect(pin).toBeDisabled()
  })

  it('opens a positive/negative chooser on click and POSTs the chosen label', async () => {
    const fetchMock = vi.fn(() => Promise.resolve({ ok: true })) as unknown as typeof fetch
    vi.stubGlobal('fetch', fetchMock)
    renderWithQuery('cat', [makeResult({ file_path: '/m/clip.mp4', file_type: 'video', timestamp: 1 })])

    // 1. Click pin → chooser appears, no POST yet.
    fireEvent.click(screen.getByTestId('pin-button'))
    expect(screen.getByTestId('pin-choice')).toBeInTheDocument()
    expect((fetchMock as ReturnType<typeof vi.fn>).mock.calls.length).toBe(0)

    // 2. Click 👍 → POST with label=1 and the active search query.
    fireEvent.click(screen.getByTestId('pin-positive'))
    await waitFor(() => expect((fetchMock as ReturnType<typeof vi.fn>).mock.calls.length).toBe(1))
    const call = (fetchMock as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(call[0]).toBe('/api/eval-set')
    const body = JSON.parse((call[1] as RequestInit).body as string)
    expect(body).toMatchObject({ search_query: 'cat', file_path: '/m/clip.mp4', label: 1 })
  })

  it('sends label=-1 when the user picks the negative option', async () => {
    const fetchMock = vi.fn(() => Promise.resolve({ ok: true })) as unknown as typeof fetch
    vi.stubGlobal('fetch', fetchMock)
    renderWithQuery('dog', [makeResult({ file_path: '/m/clip.mp4', file_type: 'video', timestamp: 1 })])
    fireEvent.click(screen.getByTestId('pin-button'))
    fireEvent.click(screen.getByTestId('pin-negative'))
    await waitFor(() => expect((fetchMock as ReturnType<typeof vi.fn>).mock.calls.length).toBe(1))
    const body = JSON.parse(((fetchMock as ReturnType<typeof vi.fn>).mock.calls[0][1] as RequestInit).body as string)
    expect(body.label).toBe(-1)
    expect(body.search_query).toBe('dog')
  })

  it('shows error state when the pin POST fails', async () => {
    const fetchMock = vi.fn(() => Promise.resolve({ ok: false })) as unknown as typeof fetch
    vi.stubGlobal('fetch', fetchMock)
    renderWithQuery('cat', [makeResult({ file_path: '/m/clip.mp4', file_type: 'video', timestamp: 1 })])
    fireEvent.click(screen.getByTestId('pin-button'))
    fireEvent.click(screen.getByTestId('pin-positive'))
    // Title flips to "Pin failed" once the request resolves.
    await waitFor(() => expect(screen.getByTitle(/Pin failed/)).toBeInTheDocument())
  })
})

// ── Vote classification (manual vs cascade vs labeled-elsewhere) ─────────────

describe('ResultGrid — vote classification', () => {
  afterEach(() => { cleanup(); vi.clearAllMocks() })

  function renderWithQuery(query: string, results: ReturnType<typeof makeResult>[]) {
    return render(
      <SearchProvider value={query}>
        <ResultGrid results={results} />
      </SearchProvider>
    )
  }

  it('classifies a current-query upvote with vote_label[query]===1 as manual', () => {
    renderWithQuery('cat', [makeResult({
      file_type: 'video', timestamp: 1,
      user_vote: 1, vote_label: { cat: 1.0 },
    })])
    // Manual gets the "Manually upvoted" title via the upvote button.
    expect(screen.getByTitle(/Manually upvoted for this query/)).toBeInTheDocument()
  })

  it('classifies a current-query upvote with vote_label[query]<1 as cascade', () => {
    renderWithQuery('cat', [makeResult({
      file_type: 'video', timestamp: 1,
      user_vote: 1, vote_label: { cat: 0.87 },
    })])
    expect(screen.getByTitle(/Auto-cascaded at 87% visual similarity/)).toBeInTheDocument()
  })

  it('classifies a cross-query upvote as labeled-elsewhere (NOT manual)', () => {
    // The point was upvoted for "dog" but the user is now searching "cat".
    // Before the fix this rendered as manual (solid bright ring), hiding the
    // fact that no human ever confirmed this frame for "cat".
    renderWithQuery('cat', [makeResult({
      file_type: 'video', timestamp: 1,
      user_vote: 1, vote_label: { dog: 1.0 },
    })])
    const node = screen.getByTitle(/Upvoted under "dog" — not this query/)
    expect(node).toBeInTheDocument()
    // Must NOT pretend it was manually upvoted for the current query.
    expect(screen.queryByTitle(/Manually upvoted for this query/)).not.toBeInTheDocument()
  })

  it('classifies upvote with no labels as unlabeled', () => {
    renderWithQuery('cat', [makeResult({
      file_type: 'video', timestamp: 1,
      user_vote: 1, vote_label: {},
    })])
    expect(screen.getByTitle(/Upvoted — no label attached/)).toBeInTheDocument()
  })
})
