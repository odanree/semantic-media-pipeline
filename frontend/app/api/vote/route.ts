import { NextRequest, NextResponse } from 'next/server'
import { request as httpRequest } from 'http'
import { request as httpsRequest } from 'https'

const API_URL = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://api:8000'

export async function POST(req: NextRequest) {
  const BACKEND_API_KEY = process.env.BACKEND_API_KEY || ''

  let body: unknown
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 })
  }

  const b = body as Record<string, unknown>
  if (!b.file_path || typeof b.file_path !== 'string') {
    return NextResponse.json({ error: 'file_path is required' }, { status: 400 })
  }
  if (typeof b.vote !== 'number' || ![0, 1, -1].includes(b.vote as number)) {
    return NextResponse.json({ error: 'vote must be 0, 1, or -1' }, { status: 400 })
  }
  if (b.audio_segment_index !== undefined && b.audio_segment_index !== null && typeof b.audio_segment_index !== 'number') {
    return NextResponse.json({ error: 'audio_segment_index must be a number or null' }, { status: 400 })
  }

  const bodyStr = JSON.stringify(body)
  const url = new URL(`${API_URL}/api/vote`)
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

    r.setTimeout(10 * 1000, () => {
      r.destroy()
      resolve(NextResponse.json({ error: 'Vote request timed out' }, { status: 504 }))
    })

    r.on('error', (err: Error) => {
      console.error('Vote proxy error:', err)
      resolve(NextResponse.json({ error: err.message }, { status: 502 }))
    })

    r.write(bodyStr)
    r.end()
  })
}
