'use client'

import { useEffect, useRef, useState } from 'react'

interface SearchResult {
  file_path: string
  file_type: string
  similarity: number
  timestamp?: number
}

interface VideoPlayerProps {
  result: SearchResult
  onClose: () => void
}

export default function VideoPlayer({ result, onClose }: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [videoError, setVideoError] = useState<string | null>(null)
  const [quality, setQuality] = useState<'proxy' | 'original'>('proxy')
  // Saved playback position — restored after src swap so the toggle feels
  // seamless rather than jumping back to 0.
  const savedTimeRef = useRef<number>(0)

  // Reset quality to proxy whenever a new result is opened
  useEffect(() => {
    setQuality('proxy')
    setVideoError(null)
    savedTimeRef.current = 0
  }, [result.file_path])

  useEffect(() => {
    if (videoRef.current && result.timestamp) {
      videoRef.current.currentTime = result.timestamp
    }
  }, [result.timestamp])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  const streamBase = process.env.NEXT_PUBLIC_STREAM_URL || 'http://localhost:8000'
  const streamSrc = `${streamBase}/api/stream?path=${encodeURIComponent(result.file_path)}&quality=${quality}`

  function toggleQuality() {
    // Save current position before React swaps the src
    if (videoRef.current) {
      savedTimeRef.current = videoRef.current.currentTime
    }
    setQuality(q => q === 'proxy' ? 'original' : 'proxy')
    setVideoError(null)
  }

  return (
    <div className="fixed inset-0 bg-black bg-opacity-75 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-gray-900 rounded-lg max-w-4xl w-full max-h-96"
        role="dialog"
        aria-modal="true"
        aria-labelledby="video-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex justify-between items-center p-4 border-b border-gray-700">
          <h3 id="video-title" className="font-semibold truncate text-sm">{result.file_path.split('/').pop()}</h3>
          <div className="flex items-center gap-3 shrink-0">
            {/* Quality badge + toggle */}
            <span className={`text-xs font-mono px-2 py-0.5 rounded ${
              quality === 'original'
                ? 'bg-yellow-500 text-black'
                : 'bg-gray-700 text-gray-300'
            }`}>
              {quality === 'original' ? '4K SRC' : '720p'}
            </span>
            <button
              onClick={toggleQuality}
              title={quality === 'proxy' ? 'Switch to original 4K source' : 'Switch to 720p proxy'}
              className="text-xs px-3 py-1 rounded bg-gray-700 hover:bg-gray-600 text-gray-200 transition whitespace-nowrap"
            >
              {quality === 'proxy' ? 'View Original' : 'View 720p'}
            </button>
            <button
              onClick={onClose}
              aria-label="Close video player"
              className="text-gray-400 hover:text-white transition"
            >
              ✕
            </button>
          </div>
        </div>

        <div className="relative bg-black aspect-video">
          {videoError ? (
            <div className="w-full h-full flex items-center justify-center text-red-400 text-center p-4">
              <div>
                <p className="font-semibold mb-2">Failed to load video</p>
                <p className="text-sm text-gray-400">{videoError}</p>
              </div>
            </div>
          ) : (
            <video
              ref={videoRef}
              src={streamSrc}
              controls
              preload="metadata"
              className="w-full h-full"
              onLoadedMetadata={() => {
                // Restore playback position after quality switch
                if (videoRef.current && savedTimeRef.current > 0) {
                  videoRef.current.currentTime = savedTimeRef.current
                }
              }}
              onError={(e) => {
                const target = e.target as HTMLVideoElement
                setVideoError(target.error?.message || 'Unknown error')
              }}
            />
          )}
        </div>

        <div className="p-4 border-t border-gray-700 text-sm text-gray-400">
          <p>
            Similarity: <span className="text-blue-400 font-semibold">{(result.similarity * 100).toFixed(1)}%</span>
          </p>
          {result.timestamp && (
            <p>
              Timestamp: <span className="text-blue-400 font-semibold">{result.timestamp.toFixed(2)}s</span>
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
