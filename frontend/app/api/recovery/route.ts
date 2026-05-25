import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

const API_URL = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://api:8000'

export async function POST(request: NextRequest) {
  const BACKEND_API_KEY = process.env.BACKEND_API_KEY || ''
  try {
    const body = await request.json()
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 30_000) // 30s — LLM fallback fires in <10s
    const response = await fetch(`${API_URL}/api/admin/recovery/scan`, {
      method: 'POST',
      cache: 'no-store',
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(BACKEND_API_KEY && { 'X-API-Key': BACKEND_API_KEY }),
      },
      body: JSON.stringify(body),
    })
    clearTimeout(timeout)

    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: 'Unknown error' }))
      return NextResponse.json(err, { status: response.status })
    }

    return NextResponse.json(await response.json())
  } catch (error) {
    console.error('Recovery scan error:', error)
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 })
  }
}
