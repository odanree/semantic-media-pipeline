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
    const protocol = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss' : 'ws'
    const host = typeof window !== 'undefined' ? window.location.host : 'localhost:3000'

    const connect = () => {
      try {
        const wsUrl = `${protocol}://${host}/api/ws/processing-status`
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
