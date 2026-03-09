import { NextResponse } from 'next/server'

const API_URL = process.env.API_URL || 'http://api:8000'

export async function POST(request: Request) {
  const BACKEND_API_KEY = process.env.BACKEND_API_KEY || ''
  try {
    const body = await request.json()
    const { query } = body

    if (!query) {
      return NextResponse.json(
        { error: 'Query is required' },
        { status: 400 }
      )
    }

    // Call backend embedding endpoint
    const response = await fetch(`${API_URL}/api/embed-text`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(BACKEND_API_KEY && { 'X-API-Key': BACKEND_API_KEY }),
      },
      body: JSON.stringify({ query }),
    })

    if (!response.ok) {
      return NextResponse.json(
        { error: 'Embedding failed' },
        { status: response.status }
      )
    }

    const data = await response.json()
    return NextResponse.json(data)
  } catch (error) {
    console.error('Embed error:', error)
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    )
  }
}
