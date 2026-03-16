import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://api:8000'

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params
  const backendUrl = `${API_URL}/api/playlist/serve/${path.join('/')}`

  try {
    const response = await fetch(backendUrl)

    if (!response.ok) {
      return new NextResponse(null, { status: response.status })
    }

    const filename = path[path.length - 1]
    const contentType = filename.endsWith('.m3u8')
      ? 'application/vnd.apple.mpegurl'
      : 'video/mp2t'

    return new NextResponse(response.body, {
      status: 200,
      headers: {
        'Content-Type': contentType,
        'Cache-Control': 'no-cache',
      },
    })
  } catch (error) {
    console.error('Playlist serve proxy error:', error)
    return new NextResponse('Internal Server Error', { status: 500 })
  }
}
