import { NextRequest, NextResponse } from 'next/server'
import path from 'path'
import fs from 'fs'

const ALLOWED_ROOTS = [
  '/mnt/source',   // host media library (read-only source)
  '/data/media',   // processed media root
]

export async function GET(request: NextRequest) {
  try {
    const filePath = request.nextUrl.searchParams.get('path')

    if (!filePath) {
      return new NextResponse('Missing path parameter', { status: 400 })
    }

    const resolvedPath = path.resolve(filePath)

    // Security: file must be within one of the allowed roots
    const allowed = ALLOWED_ROOTS.some((root) =>
      resolvedPath.startsWith(path.resolve(root))
    )
    if (!allowed) {
      return new NextResponse('Unauthorized', { status: 403 })
    }

    if (!fs.existsSync(resolvedPath)) {
      return new NextResponse('Not Found', { status: 404 })
    }

    const stats = fs.statSync(resolvedPath)
    const fileSize = stats.size

    // Infer content type from extension
    const ext = path.extname(resolvedPath).toLowerCase()
    const contentTypeMap: Record<string, string> = {
      '.mp4': 'video/mp4',
      '.mov': 'video/quicktime',
      '.avi': 'video/x-msvideo',
      '.mkv': 'video/x-matroska',
      '.webm': 'video/webm',
      '.jpg': 'image/jpeg',
      '.jpeg': 'image/jpeg',
      '.png': 'image/png',
      '.gif': 'image/gif',
      '.webp': 'image/webp',
    }
    const contentType = contentTypeMap[ext] ?? 'application/octet-stream'

    const rangeHeader = request.headers.get('range')

    if (rangeHeader) {
      const rangeMatch = rangeHeader.match(/bytes=(\d+)-(\d*)/)
      if (!rangeMatch) {
        return new NextResponse('Invalid Range header', { status: 400 })
      }

      const start = parseInt(rangeMatch[1], 10)
      const end = rangeMatch[2] ? parseInt(rangeMatch[2], 10) : fileSize - 1

      if (start >= fileSize || end >= fileSize || start > end) {
        return new NextResponse('Range Not Satisfiable', { status: 416 })
      }

      const chunkSize = end - start + 1
      const stream = fs.createReadStream(resolvedPath, { start, end })

      return new NextResponse(stream as any, {
        status: 206,
        headers: {
          'Content-Range': `bytes ${start}-${end}/${fileSize}`,
          'Content-Length': chunkSize.toString(),
          'Content-Type': contentType,
          'Accept-Ranges': 'bytes',
          'Cache-Control': 'public, max-age=3600',
        },
      })
    }

    const stream = fs.createReadStream(resolvedPath)
    return new NextResponse(stream as any, {
      status: 200,
      headers: {
        'Content-Type': contentType,
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
