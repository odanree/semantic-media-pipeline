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

    const connect = () => {
      try {
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
          console.log('📡 Connected to real-time updates');
          setIsConnected(true);
          setError(null);
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
          reconnectTimer = setTimeout(connect, 3000);
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
