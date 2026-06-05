import { NextResponse } from 'next/server'

const API_URL = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://api:8000'

// GET — eval-set readiness panel (proxies /api/stats/eval-set).
export async function GET() {
  const BACKEND_API_KEY = process.env.BACKEND_API_KEY || ''
  try {
    const response = await fetch(`${API_URL}/api/stats/eval-set`, {
      cache: 'no-store',
      headers: { ...(BACKEND_API_KEY && { 'X-API-Key': BACKEND_API_KEY }) },
    })
    if (!response.ok) {
      return NextResponse.json({ error: 'Failed to fetch eval-set stats' }, { status: response.status })
    }
    return NextResponse.json(await response.json())
  } catch (error) {
    console.error('Eval-set readiness error:', error)
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 })
  }
}
