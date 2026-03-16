/* v8 ignore file -- HLS streaming component requires real browser APIs (MediaSource, HLS.js); not testable in jsdom */
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
  const [volume, setVolume] = useState(1)
  // Track whether hls.js is managing playback — if so, suppress raw video
  // element errors (hls.js recovers from transient MSE errors internally).
  const hlsManagedRef = useRef(false)

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
        maxBufferLength: 30,
        maxMaxBufferLength: 60,
      })
      hlsManagedRef.current = true
      hls.loadSource(playlistUrl)
      hls.attachMedia(video)
      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (data.fatal) {
          setVideoError(`HLS error: ${data.details}`)
        }
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

  // Sync volume slider → video element
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    video.volume = volume
    video.muted = volume === 0
  }, [volume])

  const formatDuration = (secs: number) => {
    const m = Math.floor(secs / 60)
    const s = Math.round(secs % 60)
    return m > 0 ? `${m}m ${s}s` : `${s}s`
  }

  const volumeIcon = volume === 0 ? 'mute' : volume < 0.5 ? 'low' : 'high'

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
                // hls.js handles its own errors — suppress raw video element
                // errors in Chromium to avoid false-positive overlays
                if (hlsManagedRef.current) return
                const target = e.target as HTMLVideoElement
                const code = target.error?.code
                const msg = target.error?.message
                setVideoError(msg || (code ? `Media error ${code}` : 'Unknown playback error'))
              }}
            />
          )}
        </div>

        {/* Footer with volume control */}
        <div className="p-3 border-t border-gray-700 flex items-center gap-3 shrink-0">
          <button
            onClick={() => setVolume(v => v > 0 ? 0 : 1)}
            className="text-gray-400 hover:text-white transition shrink-0 w-6 text-center text-sm leading-none"
            aria-label={volume === 0 ? 'Unmute' : 'Mute'}
          >
            {volumeIcon === 'mute' ? (
              <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
                <path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"/>
              </svg>
            ) : volumeIcon === 'low' ? (
              <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
                <path d="M18.5 12c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM5 9v6h4l5 5V4L9 9H5z"/>
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
                <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/>
              </svg>
            )}
          </button>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={volume}
            onChange={(e) => setVolume(parseFloat(e.target.value))}
            className="w-24 accent-purple-500 cursor-pointer"
            aria-label="Volume"
          />
          <span className="text-xs text-gray-500 ml-auto">HLS stream · expires in 1 hour</span>
        </div>
      </div>
    </div>
  )
}
