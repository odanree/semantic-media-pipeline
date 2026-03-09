/**
 * Next.js proxy route tests
 *
 * Verifies that every proxy route:
 *  - Forwards the X-API-Key header to FastAPI when BACKEND_API_KEY is set
 *  - Returns 400 for missing required query/body params
 *  - Forwards the upstream HTTP status code back to the caller
 *
 * `fetch` is mocked globally so no real network calls are made.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { NextRequest } from 'next/server'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build a minimal mock Response that satisfies the fetch contract. */
function mockResponse(
  body: unknown,
  status = 200,
  headers: Record<string, string> = { 'content-type': 'application/json' }
): Response {
  const encoded = typeof body === 'string' ? body : JSON.stringify(body)
  return new Response(encoded, { status, headers })
}

/** Return the X-API-Key header value that fetch was called with (first call). */
function capturedApiKey(): string | undefined {
  const calls = (fetch as ReturnType<typeof vi.fn>).mock.calls
  if (!calls.length) return undefined
  const [, init] = calls[0] as [string, RequestInit]
  const headers = init?.headers as Record<string, string> | undefined
  return headers?.['X-API-Key']
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn())
  process.env.BACKEND_API_KEY = 'test-secret'
  process.env.API_URL = 'http://api:8000'
})

afterEach(() => {
  vi.unstubAllGlobals()
  delete process.env.BACKEND_API_KEY
})

// ---------------------------------------------------------------------------
// /api/stream
// ---------------------------------------------------------------------------

describe('GET /api/stream', () => {
  async function handler(url: string, reqInit?: RequestInit) {
    const { GET } = await import('../stream/route')
    return GET(new NextRequest(url, reqInit))
  }

  it('returns 400 when path param is missing', async () => {
    const res = await handler('http://localhost/api/stream')
    expect(res.status).toBe(400)
  })

  it('forwards X-API-Key to upstream', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse('', 200, { 'content-type': 'video/mp4' }))
    await handler('http://localhost/api/stream?path=demo%2Fvideo.mp4')
    expect(capturedApiKey()).toBe('test-secret')
  })

  it('forwards the quality param to upstream', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse('', 200, { 'content-type': 'video/mp4' }))
    await handler('http://localhost/api/stream?path=demo%2Fvideo.mp4&quality=proxy')
    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string]
    expect(url).toContain('quality=proxy')
  })

  it('does not send X-API-Key when BACKEND_API_KEY is unset', async () => {
    delete process.env.BACKEND_API_KEY
    vi.mocked(fetch).mockResolvedValue(mockResponse('', 200, { 'content-type': 'video/mp4' }))
    await handler('http://localhost/api/stream?path=demo%2Fvideo.mp4')
    expect(capturedApiKey()).toBeUndefined()
  })

  it('forwards upstream non-200 status', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse('Forbidden', 403))
    const res = await handler('http://localhost/api/stream?path=demo%2Fvideo.mp4')
    expect(res.status).toBe(403)
  })
})

// ---------------------------------------------------------------------------
// /api/thumbnail
// ---------------------------------------------------------------------------

describe('GET /api/thumbnail', () => {
  async function handler(url: string) {
    const { GET } = await import('../thumbnail/route')
    return GET(new NextRequest(url))
  }

  it('returns 400 when path param is missing', async () => {
    const res = await handler('http://localhost/api/thumbnail')
    expect(res.status).toBe(400)
  })

  it('forwards X-API-Key to upstream', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse('', 200, { 'content-type': 'image/jpeg' }))
    await handler('http://localhost/api/thumbnail?path=demo%2Fvideo.mp4&t=5')
    expect(capturedApiKey()).toBe('test-secret')
  })

  it('defaults t=0 when t param is missing', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse('', 200, { 'content-type': 'image/jpeg' }))
    await handler('http://localhost/api/thumbnail?path=demo%2Fvideo.mp4')
    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string]
    expect(url).toContain('t=0')
  })

  it('forwards upstream error status', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse('', 404))
    const res = await handler('http://localhost/api/thumbnail?path=demo%2Fvideo.mp4')
    expect(res.status).toBe(404)
  })
})

// ---------------------------------------------------------------------------
// /api/collection
// ---------------------------------------------------------------------------

describe('GET /api/collection', () => {
  async function handler() {
    const { GET } = await import('../collection/route')
    return GET()
  }

  it('returns 200 with upstream data', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse({ total: 42 }))
    const res = await handler()
    expect(res.status).toBe(200)
    const json = await res.json()
    expect(json.total).toBe(42)
  })

  it('forwards X-API-Key to upstream', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse({ total: 0 }))
    await handler()
    expect(capturedApiKey()).toBe('test-secret')
  })

  it('returns 500 on upstream error', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse('error', 500))
    const res = await handler()
    expect(res.status).toBe(500)
  })
})

// ---------------------------------------------------------------------------
// /api/status
// ---------------------------------------------------------------------------

describe('GET /api/status', () => {
  async function handler() {
    const { GET } = await import('../status/route')
    return GET()
  }

  it('returns 200 with upstream data', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse({ healthy: true }))
    const res = await handler()
    expect(res.status).toBe(200)
  })

  it('forwards X-API-Key to upstream', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse({ healthy: true }))
    await handler()
    expect(capturedApiKey()).toBe('test-secret')
  })

  it('forwards upstream error status', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse('error', 503))
    const res = await handler()
    expect(res.status).toBe(503)
  })
})

// ---------------------------------------------------------------------------
// /api/search
// ---------------------------------------------------------------------------

describe('POST /api/search', () => {
  async function handler(body: unknown) {
    const { POST } = await import('../search/route')
    return POST(
      new NextRequest('http://localhost/api/search', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      })
    )
  }

  it('returns 400 when query is missing', async () => {
    const res = await handler({})
    expect(res.status).toBe(400)
  })

  it('forwards X-API-Key to upstream', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse({ results: [] }))
    await handler({ query: 'dog' })
    expect(capturedApiKey()).toBe('test-secret')
  })

  it('uses default threshold 0.2 when not provided', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse({ results: [] }))
    await handler({ query: 'dog' })
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit]
    const sent = JSON.parse(init.body as string)
    expect(sent.threshold).toBe(0.2)
  })

  it('accepts min_similarity as alias for threshold', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse({ results: [] }))
    await handler({ query: 'cat', min_similarity: 0.35 })
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit]
    const sent = JSON.parse(init.body as string)
    expect(sent.threshold).toBe(0.35)
  })

  it('forwards upstream error status', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse('error', 422))
    const res = await handler({ query: 'dog' })
    expect(res.status).toBe(422)
  })
})

// ---------------------------------------------------------------------------
// /api/embed-text
// ---------------------------------------------------------------------------

describe('POST /api/embed-text', () => {
  async function handler(body: unknown) {
    const { POST } = await import('../embed-text/route')
    return POST(
      new Request('http://localhost/api/embed-text', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      })
    )
  }

  it('returns 400 when query is missing', async () => {
    const res = await handler({})
    expect(res.status).toBe(400)
  })

  it('forwards X-API-Key to upstream', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse({ embedding: [0.1, 0.2] }))
    await handler({ query: 'sunset' })
    expect(capturedApiKey()).toBe('test-secret')
  })

  it('forwards upstream error status', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse('error', 503))
    const res = await handler({ query: 'sunset' })
    expect(res.status).toBe(503)
  })
})
