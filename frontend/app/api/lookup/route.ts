import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://api:8000'

export async function POST(request: NextRequest) {
  const BACKEND_API_KEY = process.env.BACKEND_API_KEY || ''
  try {
    const body = await request.json()
    const { file_path, timestamp } = body

    if (!file_path) {
      return NextResponse.json({ error: 'file_path is required' }, { status: 400 })
    }

    const response = await fetch(`${API_URL}/api/lookup`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(BACKEND_API_KEY && { 'X-API-Key': BACKEND_API_KEY }),
      },
      body: JSON.stringify({ file_path, timestamp }),
    })

    if (!response.ok) {
      const error = await response.text()
      return NextResponse.json({ error: error || 'Lookup failed' }, { status: response.status })
    }

    return NextResponse.json(await response.json())
  } catch (error) {
    console.error('Lookup error:', error)
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 })
  }
}
