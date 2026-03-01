'use client'

import Image from 'next/image'
import VideoPlayer from './VideoPlayer'
import { useState } from 'react'

interface SearchResult {
  file_path: string
  file_type: string
  similarity: number
  frame_index?: number
  timestamp?: number
}

interface ResultGridProps {
  results: SearchResult[]
}

export default function ResultGrid({ results }: ResultGridProps) {
  const [selectedVideo, setSelectedVideo] = useState<SearchResult | null>(null)

  if (results.length === 0) {
    return (
      <div className="text-center p-8 text-gray-400">
        <p>No results to display</p>
      </div>
    )
  }

  return (
    <>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4" role="list">
        {results.map((result) => (
          <div
            key={`${result.file_path}-${result.frame_index || 0}`}
            className="group cursor-pointer bg-gray-800 rounded-lg overflow-hidden hover:ring-2 hover:ring-blue-500 transition"
            role="listitem"
            onClick={() => result.file_type === 'video' && setSelectedVideo(result)}
            onKeyDown={(e) => {
              if ((e.key === 'Enter' || e.key === ' ') && result.file_type === 'video') {
                setSelectedVideo(result)
              }
            }}
            tabIndex={result.file_type === 'video' ? 0 : -1}
            aria-label={`${result.file_type} with ${(result.similarity * 100).toFixed(1)}% similarity`}
          >
            <div className="relative aspect-square bg-gray-700 overflow-hidden">
              <div className="absolute inset-0 bg-gradient-to-b from-transparent to-gray-900 opacity-60"></div>

              {result.file_type === 'video' ? (
                <div className="w-full h-full flex items-center justify-center">
                  <div className="text-center">
                    <div className="text-4xl mb-2">🎥</div>
                    <p className="text-xs text-gray-300">
                      Click to play
                    </p>
                  </div>
                </div>
              ) : (
                <div className="text-4xl">🖼️</div>
              )}

              <div className="absolute bottom-2 right-2 px-2 py-1 bg-black bg-opacity-70 rounded text-xs font-semibold">
                {(result.similarity * 100).toFixed(1)}%
              </div>
            </div>

            <div className="p-3">
              <p className="text-xs text-gray-400 truncate">
                {result.file_path.split('/').pop()}
              </p>
              <p className="text-xs text-gray-500 mt-1">
                {result.file_type === 'video' && result.frame_index !== undefined
                  ? `Frame ${result.frame_index} @ ${(result.timestamp || 0).toFixed(1)}s`
                  : result.file_type === 'video'
                  ? 'Video'
                  : 'Image'}
              </p>
            </div>
          </div>
        ))}
      </div>

      {selectedVideo && (
        <VideoPlayer
          result={selectedVideo}
          onClose={() => setSelectedVideo(null)}
        />
      )}
    </>
  )
}
