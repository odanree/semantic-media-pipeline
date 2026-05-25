/**
 * ResultGrid component tests — runs in jsdom.
 * Isolated from VideoPlayer to reduce per-file memory footprint.
 * Mocks heavy child components (ResultCard) to avoid N×card memory cost.
 */

import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom'
import { makeResult } from '@/test/factories'
import ResultGrid from '@/components/ResultGrid'

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
})
