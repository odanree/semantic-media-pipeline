import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.API_URL || 'http://api:8000'
const BACKEND_API_KEY = process.env.BACKEND_API_KEY || ''

export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    const { query, limit = 20, threshold, min_similarity } = body
    // page.tsx sends min_similarity; some callers send threshold — accept both.
    // Default to 0.3 to match the UI slider default.
    const effectiveThreshold: number = min_similarity ?? threshold ?? 0.3

    // Call backend API
    const response = await fetch(`${API_URL}/api/search`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(BACKEND_API_KEY && { 'X-API-Key': BACKEND_API_KEY }),
      },
      body: JSON.stringify({ query, limit, threshold: effectiveThreshold }),
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
