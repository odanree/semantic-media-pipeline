import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://api:8000'

export async function POST(request: NextRequest) {
  const BACKEND_API_KEY = process.env.BACKEND_API_KEY || ''
  try {
    const body = await request.json()

    if (!body.clips || !Array.isArray(body.clips) || body.clips.length === 0) {
      return NextResponse.json({ error: 'clips array is required' }, { status: 400 })
    }

    const response = await fetch(`${API_URL}/api/playlist`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(BACKEND_API_KEY && { 'X-API-Key': BACKEND_API_KEY }),
      },
      body: JSON.stringify(body),
    })

    if (!response.ok) {
      const error = await response.text()
      return NextResponse.json(
        { error: error || 'Playlist generation failed' },
        { status: response.status }
      )
    }

    const data = await response.json()
    return NextResponse.json(data)
  } catch (error) {
    console.error('Playlist error:', error)
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 })
  }
}
