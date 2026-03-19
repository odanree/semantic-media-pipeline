/* v8 ignore file -- HLS streaming component requires real browser APIs (MediaSource, HLS.js); not testable in jsdom */
'use client'

import { useEffect, useRef, useState, useCallback } from 'react'

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

  const [playing, setPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [volume, setVolume] = useState(1)

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
    const video = videoRef.current
    if (!video) return

    const onTimeUpdate = () => setCurrentTime(video.currentTime)
    const onDurationChange = () => { if (isFinite(video.duration)) setDuration(video.duration) }
    const onPlay = () => setPlaying(true)
    const onPause = () => setPlaying(false)
    const onError = () => {
      if (hlsManagedRef.current) return
      const code = video.error?.code
      const msg = video.error?.message
      setVideoError(msg || (code ? `Media error ${code}` : 'Unknown playback error'))
    }

    video.addEventListener('timeupdate', onTimeUpdate)
    video.addEventListener('durationchange', onDurationChange)
    video.addEventListener('play', onPlay)
    video.addEventListener('pause', onPause)
    video.addEventListener('error', onError)

    return () => {
      video.removeEventListener('timeupdate', onTimeUpdate)
      video.removeEventListener('durationchange', onDurationChange)
      video.removeEventListener('play', onPlay)
      video.removeEventListener('pause', onPause)
      video.removeEventListener('error', onError)
    }
  }, [])

  const togglePlay = useCallback(() => {
    const video = videoRef.current
    if (!video) return
    if (video.paused) video.play().catch(() => {})
    else video.pause()
  }, [])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key === ' ') { e.preventDefault(); togglePlay() }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose, togglePlay])

  const handleSeek = (e: React.ChangeEvent<HTMLInputElement>) => {
    const video = videoRef.current
    if (!video) return
    const t = parseFloat(e.target.value)
    video.currentTime = t
    setCurrentTime(t)
  }

  const handleVolume = (e: React.ChangeEvent<HTMLInputElement>) => {
    const video = videoRef.current
    if (!video) return
    const v = parseFloat(e.target.value)
    video.volume = v
    video.muted = v === 0
    setVolume(v)
  }

  const toggleFullscreen = () => {
    const video = videoRef.current
    if (!video) return
    if (document.fullscreenElement) document.exitFullscreen()
    else video.requestFullscreen()
  }

  const fmt = (secs: number) => {
    const m = Math.floor(secs / 60)
    const s = Math.floor(secs % 60).toString().padStart(2, '0')
    return `${m}:${s}`
  }

  const formatDuration = (secs: number) => {
    const m = Math.floor(secs / 60)
    const s = Math.round(secs % 60)
    return m > 0 ? `${m}m ${s}s` : `${s}s`
  }

  const progress = duration > 0 ? (currentTime / duration) * 100 : 0

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
              preload="auto"
              className="w-full h-full object-contain cursor-pointer"
              onClick={(e) => { e.preventDefault(); togglePlay() }}
              onKeyDown={(e) => e.preventDefault()}
            />
          )}
        </div>

        {!videoError && (
          <div className="bg-gray-800 px-3 pt-2 pb-3 space-y-2">
            {/* Seek bar */}
            <div className="relative group">
              <input
                type="range"
                min={0}
                max={duration || 0}
                step={0.1}
                value={currentTime}
                onChange={handleSeek}
                className="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-gray-600"
                style={{
                  background: `linear-gradient(to right, #3b82f6 ${progress}%, #4b5563 ${progress}%)`,
                }}
              />
            </div>

            {/* Controls row */}
            <div className="flex items-center justify-between text-white">
              <div className="flex items-center gap-3">
                <button
                  onClick={togglePlay}
                  aria-label={playing ? 'Pause' : 'Play'}
                  className="hover:text-blue-400 transition text-lg w-6 text-center"
                >
                  {playing ? '⏸' : '▶'}
                </button>
                <span className="text-gray-400 text-sm">🔊</span>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={volume}
                  onChange={handleVolume}
                  aria-label="Volume"
                  className="w-24 h-1.5 rounded-full appearance-none cursor-pointer"
                  style={{
                    background: `linear-gradient(to right, #9ca3af ${volume * 100}%, #4b5563 ${volume * 100}%)`,
                  }}
                />
                <span className="text-sm text-gray-300 tabular-nums">
                  {fmt(currentTime)} / {fmt(duration)}
                </span>
              </div>
              <div className="flex items-center gap-3 text-sm text-gray-400">
                <span>{clipCount} clips · <span className="text-blue-400 font-semibold">{formatDuration(totalDurationSec)}</span></span>
                <button
                  onClick={toggleFullscreen}
                  aria-label="Fullscreen"
                  className="hover:text-white transition"
                >
                  ⛶
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
