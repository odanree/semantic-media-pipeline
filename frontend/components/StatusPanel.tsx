'use client'

import { useEffect, useState } from 'react'
import { useStatusUpdates } from '@/hooks/useStatusUpdates'

interface StatusData {
  total: number
  by_status: {
    pending: number
    processing: number
    done: number
    error: number
  }
  by_type: {
    images: number
    videos: number
  }
}

export default function StatusPanel() {
  const [status, setStatus] = useState<StatusData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [retrying, setRetrying] = useState(false)

  const { status: wsStatus, isConnected, error: wsError } = useStatusUpdates({
    onUpdate: (newStatus) => {
      setStatus(newStatus)
      setLoading(false)
      setError(null)
    },
    onError: (err) => {
      setError(err.message)
      setLoading(false)
    },
  })

  // Fallback to HTTP if WebSocket fails
  useEffect(() => {
    if (!isConnected && loading && !error) {
      const fetchStatus = async () => {
        try {
          setError(null)
          const response = await fetch('/api/status')
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`)
          }
          const data = await response.json()
          setStatus(data.files)
          setLoading(false)
        } catch (err) {
          console.error('Failed to fetch status:', err)
          setError(err instanceof Error ? err.message : 'Failed to fetch pipeline status')
          setLoading(false)
        }
      }

      const timeout = setTimeout(fetchStatus, 2000) // Give WS 2 seconds to connect
      return () => clearTimeout(timeout)
    }
  }, [isConnected, loading, error])

  // Update status from WebSocket if available
  useEffect(() => {
    if (wsStatus) {
      setStatus(wsStatus)
      setLoading(false)
    }
  }, [wsStatus])

  const handleRetry = () => {
    setRetrying(true)
    // Reload page to restart WebSocket connection
    setTimeout(() => window.location.reload(), 500)
  }

  if (loading) {
    return (
      <div className="text-center text-gray-400 p-6">
        <div className="inline-block mb-2">
          <div className="animate-spin rounded-full h-6 w-6 border-2 border-gray-600 border-t-blue-400"></div>
        </div>
        <p>Loading pipeline status...</p>
      </div>
    )
  }

  if (error && !status) {
    return (
      <div className="p-6 bg-red-900 border border-red-700 rounded-lg" role="alert">
        <p className="text-red-100 mb-4">Unable to fetch pipeline status: {error}</p>
        <button
          onClick={handleRetry}
          disabled={retrying}
          className="px-4 py-2 bg-red-700 hover:bg-red-600 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded transition text-sm font-semibold"
        >
          {retrying ? 'Retrying...' : 'Retry'}
        </button>
      </div>
    )
  }

  if (!status) {
    return (
      <div className="p-6 bg-gray-800 rounded-lg border border-gray-700">
        <p className="text-gray-400">No pipeline data available</p>
      </div>
    )
  }

  const { total, by_status, by_type } = status

  return (
    <div className="space-y-4">
      {/* Connection Status */}
      <div className="flex items-center gap-2 text-sm">
        <div
          className={`w-2 h-2 rounded-full transition-colors ${
            isConnected ? 'bg-green-500' : 'bg-yellow-500'
          }`}
          aria-label={isConnected ? 'Connected to real-time updates' : 'Attempting to connect'}
        />
        <span className="text-gray-400">
          {isConnected ? '🟢 Live Updates' : '🟡 Polling Mode'}
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="p-6 bg-gray-800 rounded-lg border border-gray-700">
          <h3 className="text-lg font-semibold mb-4">Processing Status</h3>
          <div className="space-y-3">
          <div className="flex justify-between items-center">
            <span className="text-gray-400">Total Files</span>
            <span className="text-2xl font-bold text-blue-400">{total}</span>
          </div>
          <div className="flex justify-between items-center text-sm">
            <span className="text-gray-400">Pending</span>
            <span className="text-yellow-400">{by_status.pending}</span>
          </div>
          <div className="flex justify-between items-center text-sm">
            <span className="text-gray-400">Processing</span>
            <span className="text-purple-400">{by_status.processing}</span>
          </div>
          <div className="flex justify-between items-center text-sm">
            <span className="text-gray-400">Done</span>
            <span className="text-green-400">{by_status.done}</span>
          </div>
          <div className="flex justify-between items-center text-sm">
            <span className="text-gray-400">Error</span>
            <span className="text-red-400">{by_status.error}</span>
          </div>
        </div>
      </div>

      <div className="p-6 bg-gray-800 rounded-lg border border-gray-700">
        <h3 className="text-lg font-semibold mb-4">Media Breakdown</h3>
        <div className="space-y-3">
          <div className="flex justify-between items-center">
            <span className="text-gray-400">Images</span>
            <span className="text-xl font-bold text-indigo-400">{by_type.images}</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-gray-400">Videos</span>
            <span className="text-xl font-bold text-pink-400">{by_type.videos}</span>
          </div>
          <div className="mt-4 h-2 bg-gray-700 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-indigo-500 to-pink-500"
              style={{
                width: total > 0
                  ? `${(by_type.images / total) * 100}%`
                  : '0%',
              }}
            ></div>
          </div>
        </div>
      </div>
    </div>
  )
}
