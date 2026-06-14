/**
 * /recent admin page tests — runs in jsdom.
 *
 * Covers: load + render, error state, filename filter narrowing rows,
 * filter empty state, status filter forwarding, vector badge rendering.
 */

import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import '@testing-library/jest-dom'

vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode; [k: string]: unknown }) =>
    React.createElement('a', { href, ...rest }, children),
}))

import RecentPage from '@/app/recent/page'

const ROWS = [
  {
    file_path: '/m/alpha-clip.mp4',
    file_type: 'video',
    file_size_bytes: '10485760',
    duration_secs: '125',
    width: '1920', height: '1080',
    processed_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    processing_status: 'done',
    model_version: 'clip-ViT-L-14',
    embedding_ms: 820,
    has_vector: true,
  },
  {
    file_path: '/m/beta-photo.jpg',
    file_type: 'image',
    file_size_bytes: '204800',
    duration_secs: null,
    width: '800', height: '600',
    processed_at: new Date(Date.now() - 2 * 3_600_000).toISOString(),
    processing_status: 'done',
    model_version: 'clip-ViT-L-14',
    embedding_ms: 95,
    has_vector: true,
  },
  {
    file_path: '/m/queued-clip.mp4',
    file_type: 'video',
    file_size_bytes: null,
    duration_secs: null,
    width: null, height: null,
    processed_at: null,
    processing_status: 'pending',
    model_version: null,
    embedding_ms: null,
    has_vector: false,
  },
]

function makeFetchMock(ok = true) {
  return vi.fn((url: string) => {
    if (!ok) {
      return Promise.resolve({ ok: false, status: 500, json: async () => ({ detail: 'boom' }) })
    }
    // /api/admin/recent[?…]
    if (url.includes('/api/admin/recent')) {
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ items: ROWS, limit: 50 }) })
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => ({}) })
  })
}

beforeEach(() => {
  vi.stubGlobal('fetch', makeFetchMock())
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('/recent page', () => {
  it('renders the three rows after fetch resolves', async () => {
    render(<RecentPage />)
    await waitFor(() => expect(screen.getByText('alpha-clip.mp4')).toBeInTheDocument())
    expect(screen.getByText('beta-photo.jpg')).toBeInTheDocument()
    expect(screen.getByText('queued-clip.mp4')).toBeInTheDocument()
  })

  it('shows "✓ indexed" for rows with a vector and "no vector" otherwise', async () => {
    render(<RecentPage />)
    await waitFor(() => expect(screen.getByText('alpha-clip.mp4')).toBeInTheDocument())
    expect(screen.getAllByText(/✓ indexed/).length).toBe(2)
    expect(screen.getByText(/no vector/)).toBeInTheDocument()
  })

  it('renders an error banner when the fetch fails', async () => {
    vi.stubGlobal('fetch', makeFetchMock(false))
    render(<RecentPage />)
    await waitFor(() => expect(screen.getByText(/boom/)).toBeInTheDocument())
  })

  it('narrows the table when the filename filter is typed', async () => {
    render(<RecentPage />)
    await waitFor(() => expect(screen.getByText('alpha-clip.mp4')).toBeInTheDocument())

    const filter = screen.getByPlaceholderText(/Filter by filename/i)
    fireEvent.change(filter, { target: { value: 'alpha' } })

    expect(screen.getByText('alpha-clip.mp4')).toBeInTheDocument()
    expect(screen.queryByText('beta-photo.jpg')).not.toBeInTheDocument()
    expect(screen.queryByText('queued-clip.mp4')).not.toBeInTheDocument()
    expect(screen.getByText(/1 of 3 match/)).toBeInTheDocument()
  })

  it('shows the "no rows match" hint when filter excludes every row', async () => {
    render(<RecentPage />)
    await waitFor(() => expect(screen.getByText('alpha-clip.mp4')).toBeInTheDocument())

    fireEvent.change(screen.getByPlaceholderText(/Filter by filename/i), { target: { value: 'zzzzz' } })

    expect(screen.getByText(/No rows match/)).toBeInTheDocument()
    expect(screen.getByText(/try increasing the limit/)).toBeInTheDocument()
  })

  it('forwards the status filter into the fetch URL', async () => {
    const fetchMock = makeFetchMock()
    vi.stubGlobal('fetch', fetchMock)
    render(<RecentPage />)
    await waitFor(() => expect(screen.getByText('alpha-clip.mp4')).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText('Filter by processing status'), { target: { value: 'error' } })

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((c) => c[0] as string)
      expect(urls.some((u) => u.includes('status=error'))).toBe(true)
    })
  })

  it('links the filename to the filename-search shortcut', async () => {
    render(<RecentPage />)
    await waitFor(() => expect(screen.getByText('alpha-clip.mp4')).toBeInTheDocument())
    const link = screen.getByText('alpha-clip.mp4').closest('a')
    expect(link).toHaveAttribute('href', `/?q=${encodeURIComponent('f:alpha-clip.mp4')}`)
  })
})
