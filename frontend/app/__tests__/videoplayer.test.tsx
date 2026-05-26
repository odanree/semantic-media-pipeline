/**
 * VideoPlayer component tests — runs in jsdom.
 * Isolated from ResultGrid to reduce per-file memory footprint.
 */

import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom'
import { makeResult } from '@/test/factories'
import VideoPlayer from '@/components/VideoPlayer'

// ── Global mocks (if not already defined) ────────────────────────────────────

const windowObj = window as unknown as Window & { HTMLMediaElement?: typeof HTMLMediaElement }
if (typeof windowObj.HTMLMediaElement === 'undefined') {
  windowObj.HTMLMediaElement = class {
    play = vi.fn()
    pause = vi.fn()
    load = vi.fn()
  } as unknown as typeof HTMLMediaElement
}

// ── VideoPlayer ──────────────────────────────────────────────────────────────

describe('VideoPlayer', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

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
    fireEvent.click(screen.getByRole('button', { name: /skip back 1 minute/i }))
  })

  it('skip forward button calls skip(60)', () => {
    render(<VideoPlayer result={makeResult()} onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /skip forward 1 minute/i }))
  })

  it('clicking inner dialog prevents backdrop close', () => {
    const onClose = vi.fn()
    const { container } = render(<VideoPlayer result={makeResult()} onClose={onClose} />)
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
