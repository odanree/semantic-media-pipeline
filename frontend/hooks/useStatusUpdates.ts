'use client'

import { useEffect, useState } from 'react'

export interface StatusUpdate {
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

interface UseStatusUpdatesOptions {
  onUpdate?: (status: StatusUpdate) => void
  onError?: (error: Error) => void
}

export function useStatusUpdates(options: UseStatusUpdatesOptions = {}) {
  const { onUpdate, onError } = options
  const [status, setStatus] = useState<StatusUpdate | null>(null)
  const [isConnected, setIsConnected] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    let ws: WebSocket | null = null
    let reconnectTimer: NodeJS.Timeout | null = null
    let retryCount = 0
    const MAX_RETRIES = 5
    const BASE_RETRY_DELAY = 3000 // 3 seconds

    const connect = () => {
      try {
        // Use environment variable API URL, fallback to window location if in browser
        let apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://api:8000'
        if (typeof window !== 'undefined' && !apiUrl.startsWith('http')) {
          // If API_URL is empty or not set to absolute URL, use window location
          apiUrl = `${window.location.protocol}//${window.location.host}`
        }
        
        // Convert HTTP(S) to WS(S) for WebSocket
        const wsProtocol = apiUrl.startsWith('https') ? 'wss' : 'ws'
        const apiHost = apiUrl.replace(/^https?:\/\//, '').replace(/\/$/, '')
        const wsUrl = `${wsProtocol}://${apiHost}/api/ws/processing-status`
        ws = new WebSocket(wsUrl)

        ws.onopen = () => {
          console.log('📡 Connected to status updates')
          setIsConnected(true)
          setError(null)
          retryCount = 0 // Reset retry count on successful connection
        }

        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data)
            if (data.files) {
              setStatus(data.files)
              onUpdate?.(data.files)
            }
          } catch (e) {
            console.error('Failed to parse status update:', e)
          }
        }

        ws.onerror = () => {
          const err = new Error('WebSocket connection failed')
          setError(err)
          onError?.(err)
          console.error('WebSocket error')
        }

        ws.onclose = () => {
          console.log('Status WebSocket closed - attempting reconnect...')
          setIsConnected(false)

          // Only reconnect if we haven't exceeded max retries
          if (retryCount < MAX_RETRIES) {
            retryCount++
            const delay = BASE_RETRY_DELAY * Math.pow(2, retryCount - 1) // Exponential backoff
            console.log(`Status WS Retry ${retryCount}/${MAX_RETRIES} in ${delay}ms`)
            reconnectTimer = setTimeout(connect, Math.min(delay, 30000)) // Cap at 30 seconds
          } else {
            console.log('Status WS: Max retries reached - stopping reconnection attempts')
            setError(new Error('WebSocket connection failed - max retries exceeded'))
          }
        }
      } catch (e) {
        const err = e instanceof Error ? e : new Error(String(e))
        setError(err)
        onError?.(err)
      }
    }

    connect()

    return () => {
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (ws) ws.close()
    }
  }, [onUpdate, onError])

  return { status, isConnected, error }
}
