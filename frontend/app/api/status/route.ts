import { NextResponse } from 'next/server'

const API_URL = process.env.API_URL || 'http://api:8000'

export async function GET() {
  const BACKEND_API_KEY = process.env.BACKEND_API_KEY || ''
  try {
    const response = await fetch(`${API_URL}/api/status`, {
      cache: 'no-store',
      headers: {
        ...(BACKEND_API_KEY && { 'X-API-Key': BACKEND_API_KEY }),
      },
    })

    if (!response.ok) {
      return NextResponse.json(
        { error: 'Failed to fetch status' },
        { status: response.status }
      )
    }

    const data = await response.json()
    return NextResponse.json(data)
  } catch (error) {
    console.error('Status error:', error)
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    )
  }
}
