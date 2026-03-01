/**
 * React Hook for consuming real-time media processing updates
 * 
 * Usage:
 *   const { updates, isConnected } = useMediaUpdates('ws://localhost:8000/api/ws/media-updates');
 */

'use client';

import { useEffect, useState } from 'react';

export interface MediaUpdate {
  channel: 'media_processing' | 'vector_indexed';
  id: string;
  file_path: string;
  file_type?: string;
  status?: string;
  error_message?: string;
  qdrant_point_id?: string;
  processed_at?: string;
  vector_indexed_at?: string;
  pid?: number;
}

interface UseMediaUpdatesOptions {
  maxHistorySize?: number;
  onUpdate?: (update: MediaUpdate) => void;
  onError?: (error: Error) => void;
}

export function useMediaUpdates(
  wsUrl: string,
  options: UseMediaUpdatesOptions = {}
) {
  const { maxHistorySize = 100, onUpdate, onError } = options;
  const [updates, setUpdates] = useState<MediaUpdate[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectTimer: NodeJS.Timeout | null = null;
    let retryCount = 0;
    const MAX_RETRIES = 5;
    const BASE_RETRY_DELAY = 3000; // 3 seconds

    const connect = () => {
      try {
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
          console.log('📡 Connected to real-time updates');
          setIsConnected(true);
          setError(null);
          retryCount = 0; // Reset retry count on successful connection
        };

        ws.onmessage = (event) => {
          try {
            const update: MediaUpdate = JSON.parse(event.data);
            console.log(`📬 ${update.channel}:`, update);

            setUpdates((prev) => {
              const newUpdates = [update, ...prev];
              return newUpdates.slice(0, maxHistorySize);
            });

            onUpdate?.(update);
          } catch (e) {
            console.error('Failed to parse update:', e);
          }
        };

        ws.onerror = (event) => {
          const err = new Error('WebSocket error occurred');
          setError(err);
          onError?.(err);
          console.error('WebSocket error:', event);
        };

        ws.onclose = () => {
          console.log('Disconnected - attempting reconnect...');
          setIsConnected(false);

          // Only reconnect if we haven't exceeded max retries
          if (retryCount < MAX_RETRIES) {
            retryCount++;
            const delay = BASE_RETRY_DELAY * Math.pow(2, retryCount - 1); // Exponential backoff
            console.log(`Retry ${retryCount}/${MAX_RETRIES} in ${delay}ms`);
            reconnectTimer = setTimeout(connect, Math.min(delay, 30000)); // Cap at 30 seconds
          } else {
            console.log('Max retries reached - stopping reconnection attempts');
            setError(new Error('WebSocket connection failed - max retries exceeded'));
          }
        };
      } catch (e) {
        const err = e instanceof Error ? e : new Error(String(e));
        setError(err);
        onError?.(err);
      }
    };

    connect();

    return () => {
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, [wsUrl, maxHistorySize, onUpdate, onError]);

  return { updates, isConnected, error };
}
