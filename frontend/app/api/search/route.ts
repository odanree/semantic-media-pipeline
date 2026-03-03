import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.API_URL || 'http://api:8000'

export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    const { query, limit = 20, threshold = 0.2 } = body

    // Call backend API
    const response = await fetch(`${API_URL}/api/search`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ query, limit, threshold }),
    })

    if (!response.ok) {
      const error = await response.text()
      return NextResponse.json(
        { error: error || 'Search failed' },
        { status: response.status }
      )
    }

    const data = await response.json()
    return NextResponse.json(data)
  } catch (error) {
    console.error('Search error:', error)
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    )
  }
}
