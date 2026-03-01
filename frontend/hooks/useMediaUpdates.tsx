/**
 * React Components for real-time media updates
 * 
 * Uses the useMediaUpdates hook from useMediaUpdates.ts
 */

'use client';

import { useState } from 'react';
import { useMediaUpdates, type MediaUpdate } from './useMediaUpdates';

/**
 * Component: Real-time processing status feed
 */
export function MediaUpdatesFeed() {
  const { updates, isConnected } = useMediaUpdates(
    `${process.env.NEXT_PUBLIC_API_URL}/ws/media-updates`,
    {
      onUpdate: (update) => {
        // Could trigger notifications, analytics, etc.
        if (update.status === 'completed') {
          console.log(`✅ Completed: ${update.file_path}`);
        }
      },
    }
  );

  return (
    <div className="space-y-2">
      {/* Connection status indicator */}
      <div className="flex items-center gap-2">
        <div
          className={`w-2 h-2 rounded-full ${
            isConnected ? 'bg-green-500' : 'bg-red-500'
          }`}
        />
        <span className="text-xs text-gray-600">
          {isConnected ? 'Live' : 'Disconnected'}
        </span>
      </div>

      {/* Updates list */}
      <div className="space-y-1 max-h-64 overflow-y-auto">
        {updates.length === 0 ? (
          <p className="text-xs text-gray-400 italic">No updates yet...</p>
        ) : (
          updates.map((update) => (
            <div
              key={`${update.id}-${update.channel}`}
              className="text-xs p-2 bg-gray-50 rounded border-l-2 border-blue-400"
            >
              <p className="font-mono truncate text-gray-700">
                {update.file_path.split('/').pop()}
              </p>
              <div className="flex justify-between text-gray-500">
                <span>{update.status || 'indexed'}</span>
                <span>{update.channel === 'media_processing' ? '⚙️' : '🔢'}</span>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

/**
 * Component: Processing progress dashboard
 */
export function MediaProcessingDashboard() {
  const [stats, setStats] = useState({
    processing: 0,
    completed: 0,
    failed: 0,
  });

  const { updates, isConnected } = useMediaUpdates(
    `${process.env.NEXT_PUBLIC_API_URL}/ws/processing-status`,
    {
      onUpdate: (update) => {
        setStats((prev) => {
          const newStats = { ...prev };
          if (update.status === 'processing') newStats.processing += 1;
          if (update.status === 'completed') newStats.completed += 1;
          if (update.status === 'failed') newStats.failed += 1;
          return newStats;
        });
      },
    }
  );

  const total = stats.processing + stats.completed + stats.failed;
  const completionPercent = total > 0 ? (stats.completed / total) * 100 : 0;

  return (
    <div className="space-y-4 p-4 border rounded-lg">
      <div className="flex items-center gap-2">
        <h3 className="font-semibold">Pipeline Status</h3>
        {isConnected && (
          <span className="text-xs bg-green-100 text-green-700 px-2 py-1 rounded">
            Live
          </span>
        )}
      </div>

      {/* Progress bar */}
      <div className="space-y-1">
        <div className="flex justify-between text-xs">
          <span>Overall Progress</span>
          <span className="font-mono">{completionPercent.toFixed(1)}%</span>
        </div>
        <div className="h-2 bg-gray-200 rounded overflow-hidden">
          <div
            className="h-full bg-green-500 transition-all"
            style={{ width: `${completionPercent}%` }}
          />
        </div>
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-3 gap-2 text-xs">
        <div className="p-2 bg-blue-50 rounded text-center">
          <p className="font-bold text-lg text-blue-600">
            {stats.processing}
          </p>
          <p className="text-gray-600">Processing</p>
        </div>
        <div className="p-2 bg-green-50 rounded text-center">
          <p className="font-bold text-lg text-green-600">
            {stats.completed}
          </p>
          <p className="text-gray-600">Completed</p>
        </div>
        <div className="p-2 bg-red-50 rounded text-center">
          <p className="font-bold text-lg text-red-600">{stats.failed}</p>
          <p className="text-gray-600">Failed</p>
        </div>
      </div>
    </div>
  );
}
