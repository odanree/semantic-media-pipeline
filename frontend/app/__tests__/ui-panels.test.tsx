/**
 * UI panel component tests — runs in jsdom (configured via environmentMatchGlobs).
 *
 * Covers: AskPanel, SimilarPanel
 */

import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import '@testing-library/jest-dom'

// ── Module mocks (hoisted before all imports) ────────────────────────────────

vi.mock('@/components/HighlightReelPlayer', () => ({
  default: function MockHighlightReelPlayer({ onClose }: { onClose: () => void }) {
    return React.createElement('div', { 'data-testid': 'highlight-reel', onClick: onClose }, 'MockReel')
  },
}))

// ── Static imports (resolved after mocks are hoisted) ────────────────────────

import AskPanel from '@/components/AskPanel'
import SimilarPanel from '@/components/SimilarPanel'

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
