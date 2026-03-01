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
          reconnectTimer = setTimeout(connect, 3000)
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
