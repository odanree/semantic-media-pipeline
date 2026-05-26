/**
 * UI panel component tests — runs in jsdom (configured via environmentMatchGlobs).
 *
 * Covers: AskPanel
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

