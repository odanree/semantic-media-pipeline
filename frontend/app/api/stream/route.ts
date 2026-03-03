import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.API_URL || 'http://api:8000'

export async function GET(request: NextRequest) {
  try {
    const filePath = request.nextUrl.searchParams.get('path')
    if (!filePath) {
      return new NextResponse('Missing path parameter', { status: 400 })
    }

    // Proxy to FastAPI which handles security, Range requests, and async I/O
    const upstream = await fetch(
      `${API_URL}/api/stream?path=${encodeURIComponent(filePath)}`,
      {
        headers: {
          // Forward Range header for video seeking
          ...(request.headers.get('range') ? { range: request.headers.get('range')! } : {}),
        },
      }
    )

    // Forward response headers that the browser needs for streaming
    const headers: Record<string, string> = {}
    for (const key of ['content-type', 'content-length', 'content-range', 'accept-ranges', 'cache-control']) {
      const val = upstream.headers.get(key)
      if (val) headers[key] = val
    }

    return new NextResponse(upstream.body, { status: upstream.status, headers })
  } catch (error) {
    console.error('Stream proxy error:', error)
    return new NextResponse('Internal Server Error', { status: 500 })
  }
}
