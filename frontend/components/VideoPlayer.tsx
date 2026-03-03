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
          <button
            onClick={onClose}
            aria-label="Close video player"
            className="text-gray-400 hover:text-white transition"
          >
            ✕
          </button>
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
              src={`/api/stream?path=${encodeURIComponent(result.file_path)}`}
              controls
              autoPlay
              className="w-full h-full"
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
