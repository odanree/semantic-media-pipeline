import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.API_URL || 'http://api:8000'

export async function GET(request: NextRequest) {
  const BACKEND_API_KEY = process.env.BACKEND_API_KEY || ''

  const { searchParams } = request.nextUrl
  const path = searchParams.get('path')
  const t = searchParams.get('t') ?? '0'

  if (!path) {
    return new NextResponse('Missing path parameter', { status: 400 })
  }

  try {
    const upstream = await fetch(
      `${API_URL}/api/thumbnail?path=${encodeURIComponent(path)}&t=${t}`,
      {
        headers: {
          ...(BACKEND_API_KEY && { 'X-API-Key': BACKEND_API_KEY }),
        },
      }
    )

    const headers: Record<string, string> = {}
    for (const key of ['content-type', 'content-length', 'cache-control']) {
      const val = upstream.headers.get(key)
      if (val) headers[key] = val
    }

    return new NextResponse(upstream.body, { status: upstream.status, headers })
  } catch (error) {
    console.error('Thumbnail proxy error:', error)
    return new NextResponse('Internal Server Error', { status: 500 })
  }
}
