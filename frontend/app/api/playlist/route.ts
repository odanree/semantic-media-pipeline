import { NextRequest, NextResponse } from 'next/server'
import { request as httpRequest } from 'http'
import { request as httpsRequest } from 'https'

const API_URL = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://api:8000'

// Playlist compilation blocks until all segments are encoded — can take several
// minutes for large result sets with HEVC→H264 re-encodes. fetch/undici's
// default headersTimeout (10s) fires before the server responds. Use Node's
// native http module which lets us set an explicit socket timeout instead.
export async function POST(req: NextRequest) {
  const BACKEND_API_KEY = process.env.BACKEND_API_KEY || ''

  let body: unknown
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 })
  }

  const b = body as Record<string, unknown>
  if (!b.clips || !Array.isArray(b.clips) || b.clips.length === 0) {
    return NextResponse.json({ error: 'clips array is required' }, { status: 400 })
  }

  const bodyStr = JSON.stringify(body)
  const url = new URL(`${API_URL}/api/playlist`)
  const isHttps = url.protocol === 'https:'

  return new Promise<NextResponse>((resolve) => {
    const options = {
      hostname: url.hostname,
      port: url.port ? parseInt(url.port) : isHttps ? 443 : 80,
      path: url.pathname,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(bodyStr),
        ...(BACKEND_API_KEY ? { 'X-API-Key': BACKEND_API_KEY } : {}),
      },
    }

    const r = (isHttps ? httpsRequest : httpRequest)(options, (res) => {
      const chunks: Buffer[] = []
      res.on('data', (chunk: Buffer) => chunks.push(chunk))
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString()
        try {
          resolve(NextResponse.json(JSON.parse(text), { status: res.statusCode ?? 200 }))
        } catch {
          resolve(NextResponse.json({ error: 'Bad response from API' }, { status: 502 }))
        }
      })
    })

    // 8-minute socket timeout — covers 20-clip reel with 4-concurrent semaphore @ 90s each
    r.setTimeout(8 * 60 * 1000, () => {
      r.destroy()
      resolve(NextResponse.json({ error: 'Playlist generation timed out' }, { status: 504 }))
    })

    r.on('error', (err: Error) => {
      console.error('Playlist proxy error:', err)
      resolve(NextResponse.json({ error: err.message }, { status: 502 }))
    })

    r.write(bodyStr)
    r.end()
  })
}
