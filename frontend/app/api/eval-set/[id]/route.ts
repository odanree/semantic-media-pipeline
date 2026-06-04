import { NextResponse } from 'next/server'

const API_URL = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://api:8000'

// DELETE — remove an eval-set entry by id.
export async function DELETE(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const BACKEND_API_KEY = process.env.BACKEND_API_KEY || ''
  const { id } = await ctx.params
  try {
    const response = await fetch(`${API_URL}/api/eval-set/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      headers: { ...(BACKEND_API_KEY && { 'X-API-Key': BACKEND_API_KEY }) },
    })
    if (response.status === 204) {
      return new NextResponse(null, { status: 204 })
    }
    if (!response.ok) {
      const text = await response.text()
      return NextResponse.json({ error: text || 'Delete failed' }, { status: response.status })
    }
    return new NextResponse(null, { status: 204 })
  } catch (error) {
    console.error('eval-set DELETE proxy error:', error)
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 })
  }
}
