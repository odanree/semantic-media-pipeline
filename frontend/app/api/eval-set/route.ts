import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://api:8000'

// POST — add (upsert) one eval-set entry.
export async function POST(req: NextRequest) {
  const BACKEND_API_KEY = process.env.BACKEND_API_KEY || ''
  let body: unknown
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 })
  }

  const b = body as Record<string, unknown>
  if (!b.search_query || typeof b.search_query !== 'string') {
    return NextResponse.json({ error: 'search_query is required' }, { status: 400 })
  }
  if (!b.file_path || typeof b.file_path !== 'string') {
    return NextResponse.json({ error: 'file_path is required' }, { status: 400 })
  }
  if (b.label !== 1 && b.label !== -1) {
    return NextResponse.json({ error: 'label must be 1 or -1' }, { status: 400 })
  }

  try {
    const response = await fetch(`${API_URL}/api/eval-set`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(BACKEND_API_KEY && { 'X-API-Key': BACKEND_API_KEY }),
      },
      body: JSON.stringify(body),
    })
    const text = await response.text()
    if (!response.ok) {
      return NextResponse.json({ error: text || 'Add failed' }, { status: response.status })
    }
    return NextResponse.json(JSON.parse(text), { status: response.status })
  } catch (error) {
    console.error('eval-set POST proxy error:', error)
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 })
  }
}

// GET — list entries, optional ?query=...
export async function GET(req: NextRequest) {
  const BACKEND_API_KEY = process.env.BACKEND_API_KEY || ''
  const upstream = new URL(`${API_URL}/api/eval-set`)
  const params = req.nextUrl.searchParams
  if (params.has('query')) upstream.searchParams.set('query', params.get('query')!)
  if (params.has('limit')) upstream.searchParams.set('limit', params.get('limit')!)
  if (params.has('offset')) upstream.searchParams.set('offset', params.get('offset')!)

  try {
    const response = await fetch(upstream.toString(), {
      cache: 'no-store',
      headers: { ...(BACKEND_API_KEY && { 'X-API-Key': BACKEND_API_KEY }) },
    })
    if (!response.ok) {
      return NextResponse.json({ error: 'Failed to list' }, { status: response.status })
    }
    return NextResponse.json(await response.json())
  } catch (error) {
    console.error('eval-set GET proxy error:', error)
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 })
  }
}
