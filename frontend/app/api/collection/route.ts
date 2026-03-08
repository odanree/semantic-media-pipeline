import { NextResponse } from 'next/server'

const API_URL = process.env.API_URL || 'http://api:8000'

export async function GET() {
  try {
    const response = await fetch(`${API_URL}/api/stats/collection`, {
      next: { revalidate: 60 }, // cache for 60s — collection changes slowly
    })

    if (!response.ok) {
      return NextResponse.json({ error: 'Failed to fetch collection info' }, { status: response.status })
    }

    return NextResponse.json(await response.json())
  } catch {
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 })
  }
}
