'use client'

import { useEffect, useRef, useState } from 'react'

interface HighlightReelPlayerProps {
  playlistUrl: string      // full URL to the .m3u8 manifest
  clipCount: number
  totalDurationSec: number
  onClose: () => void
}

export default function HighlightReelPlayer({
  playlistUrl,
  clipCount,
  totalDurationSec,
  onClose,
}: HighlightReelPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [videoError, setVideoError] = useState<string | null>(null)

  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    // Safari has native HLS support via <video src>
    if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = playlistUrl
      return
    }

    // Chromium/Firefox: use hls.js
    let hls: import('hls.js').default | null = null
    import('hls.js').then(({ default: Hls }) => {
      if (!Hls.isSupported()) {
        setVideoError('HLS is not supported in this browser')
        return
      }
      hls = new Hls({
        // Short buffer — clips are short, no need for large look-ahead
        maxBufferLength: 30,
        maxMaxBufferLength: 60,
      })
      hls.loadSource(playlistUrl)
      hls.attachMedia(video)
      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (data.fatal) {
          setVideoError(`HLS error: ${data.details}`)
        }
      })
    })

    return () => {
      hls?.destroy()
    }
  }, [playlistUrl])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  const formatDuration = (secs: number) => {
    const m = Math.floor(secs / 60)
    const s = Math.round(secs % 60)
    return m > 0 ? `${m}m ${s}s` : `${s}s`
  }

  return (
    <div
      className="fixed inset-0 bg-black bg-opacity-80 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-gray-900 rounded-lg max-w-4xl w-full max-h-screen flex flex-col"
        role="dialog"
        aria-modal="true"
        aria-labelledby="reel-title"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex justify-between items-center p-4 border-b border-gray-700 shrink-0">
          <div>
            <h3 id="reel-title" className="font-semibold text-sm">Highlight Reel</h3>
            <p className="text-xs text-gray-400 mt-0.5">
              {clipCount} clips · {formatDuration(totalDurationSec)}
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close highlight reel"
            className="text-gray-400 hover:text-white transition ml-4"
          >
            ✕
          </button>
        </div>

        {/* Video */}
        <div className="relative bg-black aspect-video">
          {videoError ? (
            <div className="w-full h-full flex items-center justify-center text-red-400 text-center p-4">
              <div>
                <p className="font-semibold mb-2">Failed to load reel</p>
                <p className="text-sm text-gray-400">{videoError}</p>
              </div>
            </div>
          ) : (
            <video
              ref={videoRef}
              controls
              autoPlay
              preload="auto"
              className="w-full h-full"
              onError={(e) => {
                const target = e.target as HTMLVideoElement
                setVideoError(target.error?.message || 'Unknown playback error')
              }}
            />
          )}
        </div>

        {/* Footer */}
        <div className="p-3 border-t border-gray-700 text-xs text-gray-500 shrink-0">
          HLS stream · segments expire in 1 hour
        </div>
      </div>
    </div>
  )
}
