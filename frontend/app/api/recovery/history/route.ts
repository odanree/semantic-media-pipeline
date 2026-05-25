import { NextResponse } from 'next/server'

const API_URL = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://api:8000'

export async function GET() {
  const BACKEND_API_KEY = process.env.BACKEND_API_KEY || ''
  try {
    const response = await fetch(`${API_URL}/api/admin/recovery/history?limit=50`, {
      cache: 'no-store',
      headers: {
        ...(BACKEND_API_KEY && { 'X-API-Key': BACKEND_API_KEY }),
      },
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: 'Unknown error' }))
      return NextResponse.json(err, { status: response.status })
    }
    return NextResponse.json(await response.json())
  } catch (error) {
    console.error('Recovery history error:', error)
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 })
  }
}
