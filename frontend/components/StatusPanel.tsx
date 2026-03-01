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

  // Calculate percentages
  const percentages = {
    pending: total > 0 ? (by_status.pending / total) * 100 : 0,
    processing: total > 0 ? (by_status.processing / total) * 100 : 0,
    done: total > 0 ? (by_status.done / total) * 100 : 0,
    error: total > 0 ? (by_status.error / total) * 100 : 0,
  }

  const completionPercentage = total > 0 ? (by_status.done / total) * 100 : 0

  return (
    <div className="space-y-6">
      {/* Connection Status + Title */}
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">Pipeline Status</h2>
        <div className="flex items-center gap-2">
          <div
            className={`w-3 h-3 rounded-full transition-colors ${
              isConnected ? 'bg-green-500 animate-pulse' : 'bg-yellow-500'
            }`}
            role="status"
            aria-label={isConnected ? 'Connected to real-time updates' : 'Polling for updates'}
          />
          <span className="text-sm text-gray-400">
            {isConnected ? '🟢 Live' : '🟡 Polling'}
          </span>
        </div>
      </div>

      {/* Overall Progress */}
      <div className="bg-gradient-to-br from-gray-800 to-gray-850 border border-gray-700 rounded-lg p-6">
        <div className="space-y-4">
          {/* Large progress circle */}
          <div className="flex items-center justify-between">
            <div>
              <p className="text-gray-400 text-sm font-semibold">Overall Progress</p>
              <p className="text-4xl font-bold text-white mt-1">{completionPercentage.toFixed(0)}%</p>
              <p className="text-sm text-gray-500 mt-1">
                {by_status.done} of {total} files processed
              </p>
            </div>
            {/* Circular progress */}
            <div className="relative w-32 h-32">
              <svg className="w-full h-full" viewBox="0 0 32 32">
                {/* Background circle */}
                <circle cx="16" cy="16" r="15" fill="none" stroke="#374151" strokeWidth="2" />
                {/* Progress arc (circumference = 2π*15 ≈ 94.2) */}
                <circle
                  cx="16"
                  cy="16"
                  r="15"
                  fill="none"
                  stroke="#3b82f6"
                  strokeWidth="2"
                  strokeDasharray={`${(completionPercentage / 100) * 94.2} 94.2`}
                  strokeLinecap="round"
                  style={{ transform: 'rotate(-90deg)', transformOrigin: '50% 50%' }}
                  className="transition-all duration-500"
                />
              </svg>
              <div className="absolute inset-0 flex items-center justify-center text-center">
                <div>
                  <p className="text-sm text-gray-500">remaining</p>
                  <p className="text-lg font-semibold text-blue-400">
                    {total - by_status.done}
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* Status breakdown bars */}
          <div className="space-y-2">
            <div>
              <div className="flex justify-between items-center text-sm mb-1">
                <span className="text-gray-400">Pending</span>
                <span className="text-yellow-400 font-semibold">{by_status.pending}</span>
              </div>
              <div className="h-2 bg-gray-700 rounded-full overflow-hidden">
                <div
                  className="h-full bg-yellow-500 transition-all duration-500"
                  style={{ width: `${percentages.pending}%` }}
                />
              </div>
            </div>
            <div>
              <div className="flex justify-between items-center text-sm mb-1">
                <span className="text-gray-400">Processing</span>
                <span className="text-purple-400 font-semibold">{by_status.processing}</span>
              </div>
              <div className="h-2 bg-gray-700 rounded-full overflow-hidden">
                <div
                  className="h-full bg-purple-500 animate-pulse transition-all duration-500"
                  style={{ width: `${percentages.processing}%` }}
                />
              </div>
            </div>
            <div>
              <div className="flex justify-between items-center text-sm mb-1">
                <span className="text-gray-400">Completed</span>
                <span className="text-green-400 font-semibold">{by_status.done}</span>
              </div>
              <div className="h-2 bg-gray-700 rounded-full overflow-hidden">
                <div
                  className="h-full bg-green-500 transition-all duration-500"
                  style={{ width: `${percentages.done}%` }}
                />
              </div>
            </div>
            {by_status.error > 0 && (
              <div>
                <div className="flex justify-between items-center text-sm mb-1">
                  <span className="text-gray-400">Errors</span>
                  <span className="text-red-400 font-semibold">{by_status.error}</span>
                </div>
                <div className="h-2 bg-gray-700 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-red-500 transition-all duration-500"
                    style={{ width: `${percentages.error}%` }}
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Media Breakdown */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Images Card */}
        <div className="bg-gradient-to-br from-indigo-900 to-gray-800 border border-indigo-700 rounded-lg p-6 hover:border-indigo-600 transition">
          <div className="flex items-center justify-between mb-4">
            <div>
              <p className="text-gray-400 text-sm font-semibold">Images</p>
              <p className="text-3xl font-bold text-indigo-400 mt-1">{by_type.images}</p>
              <p className="text-xs text-gray-500 mt-1">
                {total > 0 ? ((by_type.images / total) * 100).toFixed(0) : 0}% of total
              </p>
            </div>
            <div className="text-4xl opacity-20">🖼️</div>
          </div>
          <div className="h-2 bg-gray-700 rounded-full overflow-hidden">
            <div
              className="h-full bg-indigo-500 transition-all duration-500"
              style={{ width: `${total > 0 ? (by_type.images / total) * 100 : 0}%` }}
            />
          </div>
        </div>

        {/* Videos Card */}
        <div className="bg-gradient-to-br from-pink-900 to-gray-800 border border-pink-700 rounded-lg p-6 hover:border-pink-600 transition">
          <div className="flex items-center justify-between mb-4">
            <div>
              <p className="text-gray-400 text-sm font-semibold">Videos</p>
              <p className="text-3xl font-bold text-pink-400 mt-1">{by_type.videos}</p>
              <p className="text-xs text-gray-500 mt-1">
                {total > 0 ? ((by_type.videos / total) * 100).toFixed(0) : 0}% of total
              </p>
            </div>
            <div className="text-4xl opacity-20">🎥</div>
          </div>
          <div className="h-2 bg-gray-700 rounded-full overflow-hidden">
            <div
              className="h-full bg-pink-500 transition-all duration-500"
              style={{ width: `${total > 0 ? (by_type.videos / total) * 100 : 0}%` }}
            />
          </div>
        </div>
      </div>

      {/* Quick Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <StatCard
          label="Processing Rate"
          value={`${by_status.processing}`}
          unit="active"
          color="purple"
        />
        <StatCard
          label="Completion"
          value={`${completionPercentage.toFixed(0)}`}
          unit="%"
          color="green"
        />
        <StatCard
          label="Queue"
          value={`${by_status.pending}`}
          unit="waiting"
          color="yellow"
        />
        {by_status.error > 0 && (
          <StatCard
            label="Errors"
            value={`${by_status.error}`}
            unit="failed"
            color="red"
          />
        )}
      </div>
    </div>
  )
}

// Quick stat card component
function StatCard({
  label,
  value,
  unit,
  color,
}: {
  label: string
  value: string
  unit: string
  color: 'green' | 'purple' | 'yellow' | 'red'
}) {
  const colorClasses = {
    green: 'bg-green-900 border-green-700 text-green-400',
    purple: 'bg-purple-900 border-purple-700 text-purple-400',
    yellow: 'bg-yellow-900 border-yellow-700 text-yellow-400',
    red: 'bg-red-900 border-red-700 text-red-400',
  }

  return (
    <div className={`${colorClasses[color]} rounded-lg p-4 border`}>
      <p className="text-xs text-gray-400 font-semibold uppercase">{label}</p>
      <p className="text-2xl font-bold mt-1">{value}</p>
      <p className="text-xs text-gray-500 mt-1">{unit}</p>
    </div>
  )
