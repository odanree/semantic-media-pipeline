/**
 * Next.js proxy route tests
 *
 * Verifies that every proxy route:
 *  - Forwards the X-API-Key header to FastAPI when BACKEND_API_KEY is set
 *  - Returns 400 for missing required query/body params
 *  - Forwards the upstream HTTP status code back to the caller
 *  - Returns 500 on network errors (fetch/axios throws)
 *
 * `fetch` and `axios` are mocked globally — no real network calls are made.
 * `fs` is mocked for the stream/[id] filesystem route.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { NextRequest } from 'next/server'

// ---------------------------------------------------------------------------
// Module mocks — must be at top level so vitest hoists them correctly
// ---------------------------------------------------------------------------

vi.mock('axios')

// fs factory mock — Node built-in properties are non-configurable so vi.spyOn
// cannot redefine them. Using a factory gives us vi.fn() instances we own.
// The route does `import fs from 'fs'` so we must provide a `default` export.
vi.mock('fs', () => {
  const existsSync = vi.fn()
  const statSync = vi.fn()
  const createReadStream = vi.fn()
  return {
    default: { existsSync, statSync, createReadStream },
    existsSync,
    statSync,
    createReadStream,
  }
})

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
  async function handler(url: string, reqInit?: ConstructorParameters<typeof NextRequest>[1]) {
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

// ---------------------------------------------------------------------------
// /api/ask
// ---------------------------------------------------------------------------

describe('POST /api/ask', () => {
  async function handler(body: unknown) {
    const { POST } = await import('../ask/route')
    return POST(
      new NextRequest('http://localhost/api/ask', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      })
    )
  }

  it('returns 400 when question is missing', async () => {
    const res = await handler({})
    expect(res.status).toBe(400)
  })

  it('forwards X-API-Key to upstream', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse({ answer: 'test', sources: [] }))
    await handler({ question: 'What videos do I have?' })
    expect(capturedApiKey()).toBe('test-secret')
  })

  it('forwards optional limit and threshold', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse({ answer: 'test', sources: [] }))
    await handler({ question: 'dogs', limit: 5, threshold: 0.3 })
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit]
    const sent = JSON.parse(init.body as string)
    expect(sent.limit).toBe(5)
    expect(sent.threshold).toBe(0.3)
  })

  it('omits dedup when not false', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse({ answer: 'test', sources: [] }))
    await handler({ question: 'dogs' })
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit]
    const sent = JSON.parse(init.body as string)
    expect('dedup' in sent).toBe(false)
  })

  it('forwards dedup=false when explicitly set', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse({ answer: 'test', sources: [] }))
    await handler({ question: 'dogs', dedup: false })
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit]
    const sent = JSON.parse(init.body as string)
    expect(sent.dedup).toBe(false)
  })

  it('forwards upstream error status', async () => {
    vi.mocked(fetch).mockResolvedValue(mockResponse('error', 503))
    const res = await handler({ question: 'dogs' })
    expect(res.status).toBe(503)
  })
})

// ---------------------------------------------------------------------------
// Catch branch — fetch throws (network failure) for all proxy routes
// ---------------------------------------------------------------------------

describe('network failures return 500', () => {
  it('GET /api/stream — fetch throws', async () => {
    vi.mocked(fetch).mockRejectedValue(new Error('ECONNREFUSED'))
    const { GET } = await import('../stream/route')
    const res = await GET(new NextRequest('http://localhost/api/stream?path=demo%2Fvideo.mp4'))
    expect(res.status).toBe(500)
  })

  it('GET /api/thumbnail — fetch throws', async () => {
    vi.mocked(fetch).mockRejectedValue(new Error('ECONNREFUSED'))
    const { GET } = await import('../thumbnail/route')
    const res = await GET(new NextRequest('http://localhost/api/thumbnail?path=demo%2Fvideo.mp4&t=5'))
    expect(res.status).toBe(500)
  })

  it('GET /api/collection — fetch throws', async () => {
    vi.mocked(fetch).mockRejectedValue(new Error('ECONNREFUSED'))
    const { GET } = await import('../collection/route')
    const res = await GET()
    const json = await res.json()
    expect(res.status).toBe(500)
    expect(json.error).toBeTruthy()
  })

  it('GET /api/status — fetch throws', async () => {
    vi.mocked(fetch).mockRejectedValue(new Error('ECONNREFUSED'))
    const { GET } = await import('../status/route')
    const res = await GET()
    expect(res.status).toBe(500)
  })

  it('POST /api/search — fetch throws', async () => {
    vi.mocked(fetch).mockRejectedValue(new Error('ECONNREFUSED'))
    const { POST } = await import('../search/route')
    const res = await POST(new NextRequest('http://localhost/api/search', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query: 'dog' }),
    }))
    expect(res.status).toBe(500)
  })

  it('POST /api/embed-text — fetch throws', async () => {
    vi.mocked(fetch).mockRejectedValue(new Error('ECONNREFUSED'))
    const { POST } = await import('../embed-text/route')
    const res = await POST(new Request('http://localhost/api/embed-text', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query: 'sunset' }),
    }))
    expect(res.status).toBe(500)
  })

  it('POST /api/ask — fetch throws', async () => {
    vi.mocked(fetch).mockRejectedValue(new Error('ECONNREFUSED'))
    const { POST } = await import('../ask/route')
    const res = await POST(new NextRequest('http://localhost/api/ask', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ question: 'What do I have?' }),
    }))
    expect(res.status).toBe(500)
  })
})

// ---------------------------------------------------------------------------
// /api/stream/[id] — local filesystem route
// ---------------------------------------------------------------------------

describe('GET /api/stream/[id]', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let fsMock: any

  beforeEach(async () => {
    // The route uses `import fs from 'fs'` so we grab the default export
    fsMock = (await import('fs')).default
    vi.clearAllMocks()  // reset call history + return values between tests
    process.env.MEDIA_ROOT = '/data/media'
  })

  afterEach(() => {
    delete process.env.MEDIA_ROOT
  })

  async function handler(id: string, rangeHeader?: string) {
    const { GET } = await import('../stream/[id]/route')
    const headers: Record<string, string> = {}
    if (rangeHeader) headers['range'] = rangeHeader
    return GET(
      new NextRequest(`http://localhost/api/stream/${encodeURIComponent(id)}`, { headers }),
      { params: Promise.resolve({ id }) }
    )
  }

  it('returns 403 for path traversal attempt', async () => {
    const res = await handler('../../../etc/passwd')
    expect(res.status).toBe(403)
  })

  it('returns 404 when file does not exist', async () => {
    fsMock.existsSync.mockReturnValue(false)
    const res = await handler('video.mp4')
    expect(res.status).toBe(404)
  })

  it('returns 400 for malformed Range header', async () => {
    fsMock.existsSync.mockReturnValue(true)
    fsMock.statSync.mockReturnValue({ size: 1000 })
    const res = await handler('video.mp4', 'invalid-range')
    expect(res.status).toBe(400)
  })

  it('returns 416 for out-of-bounds range', async () => {
    fsMock.existsSync.mockReturnValue(true)
    fsMock.statSync.mockReturnValue({ size: 100 })
    const res = await handler('video.mp4', 'bytes=500-999')
    expect(res.status).toBe(416)
  })

  it('returns 206 for range request with no end byte (bytes=START-)', async () => {
    fsMock.existsSync.mockReturnValue(true)
    fsMock.statSync.mockReturnValue({ size: 1000 })
    fsMock.createReadStream.mockReturnValue(new ReadableStream())
    const res = await handler('video.mp4', 'bytes=0-')  // open-ended range
    expect(res.status).toBe(206)
    expect(res.headers.get('content-range')).toBe('bytes 0-999/1000')
  })

  it('returns 206 for valid range request', async () => {
    fsMock.existsSync.mockReturnValue(true)
    fsMock.statSync.mockReturnValue({ size: 1000 })
    fsMock.createReadStream.mockReturnValue(new ReadableStream())
    const res = await handler('video.mp4', 'bytes=0-499')
    expect(res.status).toBe(206)
    expect(res.headers.get('content-range')).toBe('bytes 0-499/1000')
  })

  it('returns 200 for full file request (no range)', async () => {
    fsMock.existsSync.mockReturnValue(true)
    fsMock.statSync.mockReturnValue({ size: 1000 })
    fsMock.createReadStream.mockReturnValue(new ReadableStream())
    const res = await handler('video.mp4')
    expect(res.status).toBe(200)
    expect(res.headers.get('accept-ranges')).toBe('bytes')
  })

  it('returns 500 when fs throws', async () => {
    fsMock.existsSync.mockImplementation(() => { throw new Error('EPERM') })
    const res = await handler('video.mp4')
    expect(res.status).toBe(500)
  })
})

// ---------------------------------------------------------------------------
// app/actions/search.ts — server actions (uses axios)
// ---------------------------------------------------------------------------

describe('server actions (actions/search.ts)', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let axiosMock: any

  beforeEach(async () => {
    axiosMock = vi.mocked(await import('axios'))
    process.env.BACKEND_API_KEY = 'test-secret'
    process.env.NEXT_PUBLIC_API_URL = 'http://api:8000'
  })

  describe('embedQuery', () => {
    it('returns embedding array on success', async () => {
      axiosMock.default.post = vi.fn().mockResolvedValue({ data: { embedding: [0.1, 0.2, 0.3] } })
      const { embedQuery } = await import('../../actions/search')
      const result = await embedQuery('sunset')
      expect(result).toEqual([0.1, 0.2, 0.3])
    })

    it('forwards X-API-Key in request', async () => {
      axiosMock.default.post = vi.fn().mockResolvedValue({ data: { embedding: [] } })
      const { embedQuery } = await import('../../actions/search')
      await embedQuery('sunset')
      const [, , config] = (axiosMock.default.post as ReturnType<typeof vi.fn>).mock.calls[0]
      expect(config.headers['X-API-Key']).toBe('test-secret')
    })

    it('throws on axios error', async () => {
      axiosMock.default.post = vi.fn().mockRejectedValue(new Error('Network Error'))
      const { embedQuery } = await import('../../actions/search')
      await expect(embedQuery('sunset')).rejects.toThrow('Failed to embed query')
    })
  })

  describe('searchMedia', () => {
    it('returns search results on success', async () => {
      const mockResults = { results: [{ file_path: 'video.mp4', score: 0.9 }] }
      axiosMock.default.post = vi.fn().mockResolvedValue({ data: mockResults })
      const { searchMedia } = await import('../../actions/search')
      const result = await searchMedia('dog', 10, 0.2)
      expect(result).toEqual(mockResults)
    })

    it('forwards X-API-Key in request', async () => {
      axiosMock.default.post = vi.fn().mockResolvedValue({ data: { results: [] } })
      const { searchMedia } = await import('../../actions/search')
      await searchMedia('dog')
      const [, , config] = (axiosMock.default.post as ReturnType<typeof vi.fn>).mock.calls[0]
      expect(config.headers['X-API-Key']).toBe('test-secret')
    })

    it('sends correct default threshold 0.2', async () => {
      axiosMock.default.post = vi.fn().mockResolvedValue({ data: { results: [] } })
      const { searchMedia } = await import('../../actions/search')
      await searchMedia('dog')
      const [, body] = (axiosMock.default.post as ReturnType<typeof vi.fn>).mock.calls[0]
      expect(body.threshold).toBe(0.2)
    })

    it('throws on axios error', async () => {
      axiosMock.default.post = vi.fn().mockRejectedValue(new Error('Network Error'))
      const { searchMedia } = await import('../../actions/search')
      await expect(searchMedia('dog')).rejects.toThrow('Failed to search media')
    })

    it('does not send X-API-Key when BACKEND_API_KEY is unset', async () => {
      delete process.env.BACKEND_API_KEY
      axiosMock.default.post = vi.fn().mockResolvedValue({ data: { results: [] } })
      const { searchMedia } = await import('../../actions/search')
      await searchMedia('dog')
      const [, , config] = (axiosMock.default.post as ReturnType<typeof vi.fn>).mock.calls[0]
      expect(config.headers['X-API-Key']).toBeUndefined()
    })
  })
})

// ---------------------------------------------------------------------------
// app/api/debug/route.ts
// ---------------------------------------------------------------------------

describe('GET /api/debug', () => {
  it('returns json with apiUrl and timestamp', async () => {
    process.env.API_URL = 'http://custom-api:9000'
    const { GET } = await import('../debug/route')
    const res = await GET()
    const body = await res.json()
    expect(res.status).toBe(200)
    expect(body.apiUrl).toBe('http://custom-api:9000')
    expect(typeof body.timestamp).toBe('string')
    expect(body.nodeEnv).toBeDefined()
  })

  it('falls back to default api url when env var is unset', async () => {
    delete process.env.API_URL
    const { GET } = await import('../debug/route')
    const res = await GET()
    const body = await res.json()
    expect(body.apiUrl).toBe('http://api:8000')
  })
})
