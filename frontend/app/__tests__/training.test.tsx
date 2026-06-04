/**
 * Training dashboard page tests — runs in jsdom.
 *
 * Covers: TrainingPage loading, error, and data states + source-split
 * surfacing + held-out eval-set panel.
 */

import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'

vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode; [k: string]: unknown }) =>
    React.createElement('a', { href, ...rest }, children),
}))

import TrainingPage from '@/app/training/page'

const SAMPLE_DATA = {
  queries: [
    { query: 'cat on roof', positives: 10,  positives_direct: 8,  positives_cascade: 2,
      negatives: 5,  negatives_direct: 5,  negatives_cascade: 0, ratio: 0.5, tier: 'good',  tier_direct: 'good' },
    { query: 'dog running', positives: 100, positives_direct: 10, positives_cascade: 90,
      negatives: 20, negatives_direct: 20, negatives_cascade: 0, ratio: 0.2, tier: 'great', tier_direct: 'good' },
  ],
  totals: {
    queries: 2, positives: 110, positives_direct: 18, positives_cascade: 92,
    negatives: 25, negatives_direct: 25, negatives_cascade: 0,
    ratio: 0.23, ratio_direct: 1.0,
  },
  tiers: {
    queries: 'good', positives: 'great', positives_direct: 'needs_data',
    negatives: 'good', negatives_direct: 'good', ratio: 'needs_data', ratio_direct: 'best',
  },
  goals: {
    queries:   { good: 10, great: 25, best: 50 },
    positives: { good: 50, great: 150, best: 300 },
    negatives: { good: 30, great: 90, best: 200 },
    ratio:     { good: 0.3, great: 0.6, best: 0.9 },
  },
}

const EVAL_DATA = {
  queries: [
    { query: 'cat on roof', positives: 30, negatives: 10, total: 40 },
  ],
  totals: { queries: 1, positives: 30, negatives: 10, total: 40 },
  tiers:  { queries: 'needs_data', positives: 'good', negatives: 'needs_data' },
  goals: {
    queries:   { good: 5,   great: 20,  best: 50 },
    positives: { good: 100, great: 500, best: 2000 },
    negatives: { good: 100, great: 500, best: 2000 },
  },
}

// Route fetches by URL so the page can render training + eval panels independently.
function makeFetchMock({ trainingOk = true, evalOk = true } = {}) {
  return vi.fn((url: string) => {
    if (url.includes('/api/eval-set/readiness')) {
      return Promise.resolve({ ok: evalOk, json: () => Promise.resolve(EVAL_DATA) }) as unknown as Response
    }
    return Promise.resolve({ ok: trainingOk, json: () => Promise.resolve(SAMPLE_DATA) }) as unknown as Response
  }) as unknown as typeof fetch
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn())
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('TrainingPage', () => {
  it('shows a loading spinner initially', () => {
    vi.mocked(fetch).mockReturnValue(new Promise(() => {}))
    render(<TrainingPage />)
    expect(screen.getByText(/Loading stats/i)).toBeTruthy()
  })

  it('shows error message when training fetch fails', async () => {
    vi.stubGlobal('fetch', makeFetchMock({ trainingOk: false }))
    render(<TrainingPage />)
    await waitFor(() => screen.getByText(/Could not load training stats/i))
  })

  it('renders stat cards when data loads', async () => {
    vi.stubGlobal('fetch', makeFetchMock())
    render(<TrainingPage />)
    await waitFor(() => expect(screen.getAllByText('Distinct queries').length).toBeGreaterThan(0))
    expect(screen.getAllByText('Positive votes').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Negative votes').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Neg / Pos ratio').length).toBeGreaterThan(0)
  })

  it('surfaces direct vs cascade label provenance for positives', async () => {
    vi.stubGlobal('fetch', makeFetchMock())
    render(<TrainingPage />)
    const callout = await screen.findByTestId('cascade-share')
    // 18 direct + 92 cascade out of 110 total → 84% cascade.
    expect(callout.textContent).toMatch(/18 direct human positives/)
    expect(callout.textContent).toMatch(/92 cascade-propagated/)
    expect(callout.textContent).toMatch(/84% cascade/)
  })

  it('shows the direct-only sub-stat inside the Positives card', async () => {
    vi.stubGlobal('fetch', makeFetchMock())
    render(<TrainingPage />)
    // Sub-row label appears under both Positives and Negatives cards.
    await waitFor(() => expect(screen.getAllByText(/Direct \(human\) only/).length).toBeGreaterThanOrEqual(2))
    // Direct-only ratio appears under the ratio card.
    expect(screen.getByText(/Direct-only ratio/)).toBeTruthy()
  })

  it('renders per-query table rows with direct/cascade split', async () => {
    vi.stubGlobal('fetch', makeFetchMock())
    render(<TrainingPage />)
    await waitFor(() => screen.getByText('cat on roof'))
    expect(screen.getByText('dog running')).toBeTruthy()
    // dog has 10 direct + 90 cascade — both numbers should appear in the row.
    const rows = screen.getAllByRole('row')
    const dogRow = rows.find(r => r.textContent?.includes('dog running'))!
    expect(dogRow.textContent).toMatch(/10/)
    expect(dogRow.textContent).toMatch(/90/)
  })

  it('renders the held-out eval-set panel with three stat cards', async () => {
    vi.stubGlobal('fetch', makeFetchMock())
    render(<TrainingPage />)
    const heading = await screen.findByTestId('eval-section-heading')
    expect(heading.textContent).toMatch(/Held-out evaluation set/)
    const grid = await screen.findByTestId('eval-stat-grid')
    // 3 cards × the existing labels appear inside; we just check the grid is rendered.
    expect(grid).toBeTruthy()
  })

  it('shows a fallback when eval-set readiness call fails', async () => {
    vi.stubGlobal('fetch', makeFetchMock({ evalOk: false }))
    render(<TrainingPage />)
    await waitFor(() => screen.getByText(/Eval-set stats unavailable/))
  })
})
