/* v8 ignore file -- HLS streaming component requires real browser APIs (MediaSource, HLS.js); not testable in jsdom */
'use client'

import { useEffect, useRef, useState } from 'react'

interface HighlightReelPlayerProps {
  playlistUrl: string
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
  const hlsManagedRef = useRef(false)

  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    let hls: import('hls.js').default | null = null
    import('hls.js').then(({ default: Hls }) => {
      if (!Hls.isSupported()) {
        setVideoError('HLS is not supported in this browser')
        return
      }
      hls = new Hls({ maxBufferLength: 30, maxMaxBufferLength: 60 })
      hlsManagedRef.current = true
      hls.loadSource(playlistUrl)
      hls.attachMedia(video)
      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (data.fatal) setVideoError(`HLS error: ${data.details}`)
      })
    })

    return () => {
      hlsManagedRef.current = false
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
      className="fixed inset-0 bg-black bg-opacity-75 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-gray-900 rounded-lg max-w-2xl w-full max-h-[80vh] flex flex-col overflow-hidden"
        role="dialog"
        aria-modal="true"
        aria-labelledby="reel-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex justify-between items-center p-4 border-b border-gray-700">
          <h3 id="reel-title" className="font-semibold truncate text-sm">Highlight Reel</h3>
          <button
            onClick={onClose}
            aria-label="Close highlight reel"
            className="text-gray-400 hover:text-white transition ml-4 shrink-0"
          >
            ✕
          </button>
        </div>

        <div className="relative bg-black flex-1 min-h-0 overflow-hidden">
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
              className="w-full h-full object-contain"
              onError={(e) => {
                if (hlsManagedRef.current) return
                const target = e.target as HTMLVideoElement
                const code = target.error?.code
                const msg = target.error?.message
                setVideoError(msg || (code ? `Media error ${code}` : 'Unknown playback error'))
              }}
            />
          )}
        </div>

        <div className="p-4 border-t border-gray-700 text-sm text-gray-400">
          <p>
            {clipCount} clips · <span className="text-blue-400 font-semibold">{formatDuration(totalDurationSec)}</span>
          </p>
        </div>
      </div>
    </div>
  )
}
