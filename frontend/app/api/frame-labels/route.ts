import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://api:8000'

export async function POST(request: NextRequest) {
  const BACKEND_API_KEY = process.env.BACKEND_API_KEY || ''
  try {
    const body = await request.json()
    const { file_path, timestamp, top_k = 8 } = body

    if (!file_path) {
      return NextResponse.json({ error: 'file_path is required' }, { status: 400 })
    }

    const response = await fetch(`${API_URL}/api/frame-labels`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(BACKEND_API_KEY && { 'X-API-Key': BACKEND_API_KEY }),
      },
      body: JSON.stringify({ file_path, timestamp, top_k }),
    })

    if (!response.ok) {
      const error = await response.text()
      return NextResponse.json({ error: error || 'Frame labels lookup failed' }, { status: response.status })
    }

    const data = await response.json()
    return NextResponse.json(data)
  } catch (error) {
    console.error('Frame labels error:', error)
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 })
  }
}
