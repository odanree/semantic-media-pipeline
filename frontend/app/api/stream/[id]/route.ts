import { NextRequest, NextResponse } from 'next/server'
import path from 'path'
import fs from 'fs'

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params
    const fileId = decodeURIComponent(id)
    const mediaRoot = process.env.MEDIA_ROOT || '/data/media'
    const filePath = path.join(mediaRoot, fileId)

    // Security: ensure file is within media root
    const resolvedPath = path.resolve(filePath)
    const resolvedRoot = path.resolve(mediaRoot)

    if (!resolvedPath.startsWith(resolvedRoot)) {
      return new NextResponse('Unauthorized', { status: 403 })
    }

    // Check if file exists
    if (!fs.existsSync(resolvedPath)) {
      return new NextResponse('Not Found', { status: 404 })
    }

    // Get file stats
    const stats = fs.statSync(resolvedPath)
    const fileSize = stats.size

    // Handle Range requests for streaming
    const rangeHeader = request.headers.get('range')

    if (rangeHeader) {
      const rangeMatch = rangeHeader.match(/bytes=(\d+)-(\d*)/)
      if (!rangeMatch) {
        return new NextResponse('Invalid Range header', { status: 400 })
      }

      const start = parseInt(rangeMatch[1], 10)
      const end = rangeMatch[2]
        ? parseInt(rangeMatch[2], 10)
        : fileSize - 1

      if (start >= fileSize || end >= fileSize || start > end) {
        return new NextResponse('Invalid Range', { status: 416 })
      }

      const chunkSize = end - start + 1
      const stream = fs.createReadStream(resolvedPath, { start, end })

      return new NextResponse(stream as unknown as BodyInit, {
        status: 206,
        headers: {
          'Content-Range': `bytes ${start}-${end}/${fileSize}`,
          'Content-Length': chunkSize.toString(),
          'Content-Type': 'video/mp4',
          'Accept-Ranges': 'bytes',
          'Cache-Control': 'public, max-age=3600',
        },
      })
    }

    // If no Range header, return full file
    const stream = fs.createReadStream(resolvedPath)

    return new NextResponse(stream as unknown as BodyInit, {
      status: 200,
      headers: {
        'Content-Type': 'video/mp4',
        'Content-Length': fileSize.toString(),
        'Accept-Ranges': 'bytes',
        'Cache-Control': 'public, max-age=3600',
      },
    })
  } catch (error) {
    console.error('Stream error:', error)
    return new NextResponse('Internal Server Error', { status: 500 })
  }
}
