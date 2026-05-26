/**
 * Training dashboard page tests — runs in jsdom.
 *
 * Covers: TrainingPage loading, error, and data states.
 * Also exercises the ProgressBar and StatCard sub-components via rendering.
 */

import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'

// Next.js Link is a client component — stub it so we don't need the full
// Next.js router context in tests.
vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode; [k: string]: unknown }) =>
    React.createElement('a', { href, ...rest }, children),
}))

import TrainingPage from '@/app/training/page'

const SAMPLE_DATA = {
  queries: [
    { query: 'cat on roof', positives: 10, negatives: 5, ratio: 0.5, tier: 'good' as const },
    { query: 'dog running', positives: 25, negatives: 20, ratio: 0.8, tier: 'great' as const },
  ],
  totals: { queries: 2, positives: 35, negatives: 25, ratio: 0.71 },
  tiers: { queries: 'good', positives: 'great', negatives: 'good', ratio: 'good' },
  goals: {
    queries:   { good: 10, great: 25, best: 50 },
    positives: { good: 50, great: 150, best: 300 },
    negatives: { good: 30, great: 90, best: 200 },
    ratio:     { good: 0.3, great: 0.6, best: 0.9 },
  },
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn())
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('TrainingPage', () => {
  it('shows a loading spinner initially', () => {
    // fetch never resolves in this test
    vi.mocked(fetch).mockReturnValue(new Promise(() => {}))
    render(<TrainingPage />)
    expect(screen.getByText(/Loading stats/i)).toBeTruthy()
  })

  it('shows error message when fetch fails', async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: false, json: () => Promise.resolve({}) } as Response)
    render(<TrainingPage />)
    await waitFor(() => screen.getByText(/Could not load training stats/i))
  })

  it('renders stat cards when data loads', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(SAMPLE_DATA),
    } as Response)
    render(<TrainingPage />)
    await waitFor(() => expect(screen.getAllByText('Distinct queries').length).toBeGreaterThan(0))
    expect(screen.getAllByText('Positive votes').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Negative votes').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Neg / Pos ratio').length).toBeGreaterThan(0)
  })

  it('renders per-query table rows', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(SAMPLE_DATA),
    } as Response)
    render(<TrainingPage />)
    await waitFor(() => screen.getByText('cat on roof'))
    expect(screen.getByText('dog running')).toBeTruthy()
  })

  it('renders tier labels in the query table', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(SAMPLE_DATA),
    } as Response)
    render(<TrainingPage />)
    // Multiple 'Good' and 'Great' labels appear (stat cards + table rows)
    await waitFor(() => expect(screen.getAllByText('Good').length).toBeGreaterThan(0))
    expect(screen.getAllByText('Great').length).toBeGreaterThan(0)
  })
})
