'use client'

import { useEffect, useRef, useState } from 'react'

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

  // Store latest callbacks in refs so the WebSocket effect never restarts due to callback identity changes
  const onUpdateRef = useRef(onUpdate)
  const onErrorRef = useRef(onError)
  useEffect(() => {
    onUpdateRef.current = onUpdate
    onErrorRef.current = onError
  })

  useEffect(() => {
    let ws: WebSocket | null = null
    let reconnectTimer: NodeJS.Timeout | null = null
    let retryCount = 0
    const MAX_RETRIES = 5
    const BASE_RETRY_DELAY = 3000 // 3 seconds

    const connect = () => {
      try {
        // Determine API URL based on environment
        let apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
        
        // In browser context: convert Docker internal hostname to localhost
        if (typeof window !== 'undefined') {
          if (apiUrl.includes('api:8000')) {
            // Docker internal: convert to localhost for browser access
            apiUrl = 'http://localhost:8000'
          } else if (!apiUrl.startsWith('http')) {
            // Fallback to current location if no valid URL provided
            apiUrl = `${window.location.protocol}//${window.location.hostname}:8000`
          }
        }
        
        // Convert HTTP(S) to WS(S) for WebSocket
        const wsProtocol = apiUrl.startsWith('https') ? 'wss' : 'ws'
        const apiHost = apiUrl.replace(/^https?:\/\//, '').replace(/\/$/, '')
        const wsUrl = `${wsProtocol}://${apiHost}/api/ws/processing-status`
        
        console.log(`📡 Connecting to WebSocket: ${wsUrl}`)
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
              onUpdateRef.current?.(data.files)
            }
          } catch (e) {
            console.error('Failed to parse status update:', e)
          }
        }

        ws.onerror = () => {
          const err = new Error('WebSocket connection failed')
          setError(err)
          onErrorRef.current?.(err)
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
        onErrorRef.current?.(err)
      }
    }

    connect()

    return () => {
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (ws) ws.close()
    }
  }, []) // Empty deps: connect once, never restart due to callback identity changes

  return { status, isConnected, error }
}
