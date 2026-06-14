'use client'

import { useState, useEffect, useCallback, useMemo } from 'react'
import Link from 'next/link'

interface RecentItem {
  file_path: string
  file_type: string
  file_size_bytes: string | null
  duration_secs: string | null
  width: string | null
  height: string | null
  processed_at: string | null
  processing_status: string
  model_version: string | null
  embedding_ms: number | null
  has_vector: boolean
}

interface RecentResponse {
  items: RecentItem[]
  limit: number
}

const STREAM_BASE = process.env.NEXT_PUBLIC_STREAM_URL || 'http://localhost:8000'

const STATUS_COLOR: Record<string, string> = {
  done:       'text-green-400',
  processing: 'text-blue-400',
  pending:    'text-gray-400',
  error:      'text-red-400',
}

function formatBytes(s: string | null): string {
  if (!s) return '—'
  const n = Number(s)
  if (!Number.isFinite(n) || n <= 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let u = 0
  while (v >= 1024 && u < units.length - 1) { v /= 1024; u++ }
  return `${v.toFixed(v >= 10 || u === 0 ? 0 : 1)} ${units[u]}`
}

function formatDuration(s: string | null): string {
  if (!s) return '—'
  const secs = Number(s)
  if (!Number.isFinite(secs) || secs <= 0) return '—'
  const m = Math.floor(secs / 60)
  const r = Math.round(secs % 60)
  return m > 0 ? `${m}m ${r}s` : `${r}s`
}

function relativeTime(iso: string | null): string {
  if (!iso) return '—'
  const t = new Date(iso).getTime()
  const diff = Date.now() - t
  if (diff < 60_000) return 'just now'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`
  return `${Math.floor(diff / 86_400_000)}d ago`
}

export default function RecentPage() {
  const [items, setItems] = useState<RecentItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [limit, setLimit] = useState(50)
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [lastFetched, setLastFetched] = useState<Date | null>(null)
  const [filenameFilter, setFilenameFilter] = useState('')

  const filteredItems = useMemo(() => {
    const q = filenameFilter.trim().toLowerCase()
    if (!q) return items
    return items.filter((it) => it.file_path.toLowerCase().includes(q))
  }, [items, filenameFilter])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ limit: String(limit) })
      if (statusFilter) params.set('status', statusFilter)
      const resp = await fetch(`/api/admin/recent?${params}`)
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}))
        throw new Error(err.detail || `Server error ${resp.status}`)
      }
      const data: RecentResponse = await resp.json()
      setItems(data.items)
      setLastFetched(new Date())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }, [limit, statusFilter])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (!autoRefresh) return
    const id = setInterval(load, 10_000)
    return () => clearInterval(id)
  }, [autoRefresh, load])

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">Recently Indexed</h1>
          <p className="text-sm text-gray-400 mt-1">
            Media files ordered by indexing completion time (newest first)
          </p>
        </div>
        <Link href="/" className="text-sm text-blue-400 hover:text-blue-300 transition">
          ← Back to search
        </Link>
      </div>

      <div className="flex flex-wrap items-center gap-3 mb-6">
        <input
          type="text"
          value={filenameFilter}
          onChange={(e) => setFilenameFilter(e.target.value)}
          placeholder="Filter by filename or path…"
          className="px-3 py-2 bg-gray-800 border border-gray-700 rounded text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-blue-500 min-w-[260px]"
          aria-label="Filter rows by filename"
        />
        <select
          value={limit}
          onChange={(e) => setLimit(Number(e.target.value))}
          className="px-3 py-2 bg-gray-800 border border-gray-700 rounded text-sm text-gray-200"
          aria-label="Number of items"
        >
          <option value={25}>25</option>
          <option value={50}>50</option>
          <option value={100}>100</option>
          <option value={250}>250</option>
          <option value={500}>500</option>
          <option value={1000}>1000</option>
          <option value={2500}>2500</option>
          <option value={10000}>All (up to 10k)</option>
        </select>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="px-3 py-2 bg-gray-800 border border-gray-700 rounded text-sm text-gray-200"
          aria-label="Filter by processing status"
        >
          <option value="">All statuses</option>
          <option value="done">done</option>
          <option value="processing">processing</option>
          <option value="pending">pending</option>
          <option value="error">error</option>
        </select>
        <button
          onClick={load}
          disabled={loading}
          className="px-3 py-2 rounded text-sm font-semibold bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50"
        >
          {loading ? '⟳ Refreshing…' : '⟳ Refresh'}
        </button>
        <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
            className="accent-blue-500"
          />
          Auto-refresh (10s)
        </label>
        {lastFetched && (
          <span className="text-xs text-gray-500 ml-auto">
            Updated {lastFetched.toLocaleTimeString()}
          </span>
        )}
      </div>

      {error && (
        <div className="p-4 rounded-lg bg-red-900/40 border border-red-700 text-red-300 text-sm mb-6">
          {error}
        </div>
      )}

      {!error && !loading && (
        <div className="mb-3 text-xs text-gray-500">
          {filenameFilter
            ? `${filteredItems.length} of ${items.length} match "${filenameFilter}"`
            : `${items.length} item${items.length === 1 ? '' : 's'}`}
        </div>
      )}

      {!error && items.length === 0 && !loading && (
        <div className="p-8 text-center text-gray-500 border border-gray-800 rounded-lg">
          No media files match the current filter.
        </div>
      )}

      {items.length > 0 && filteredItems.length === 0 && (
        <div className="p-8 text-center text-gray-500 border border-gray-800 rounded-lg">
          No rows match &quot;{filenameFilter}&quot; in the {items.length} most-recently-indexed files.
          <div className="text-xs mt-2 text-gray-600">
            That file may exist but isn&apos;t in this window — try increasing the limit, or clearing the filter.
          </div>
        </div>
      )}

      {filteredItems.length > 0 && (
        <div className="rounded-lg border border-gray-700 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-800 text-gray-400 text-xs uppercase tracking-wide">
              <tr>
                <th className="px-3 py-3 text-left">Preview</th>
                <th className="px-3 py-3 text-left">File</th>
                <th className="px-3 py-3 text-left">Type</th>
                <th className="px-3 py-3 text-right">Size</th>
                <th className="px-3 py-3 text-right">Duration</th>
                <th className="px-3 py-3 text-left">Status</th>
                <th className="px-3 py-3 text-right">Embed (ms)</th>
                <th className="px-3 py-3 text-left">Vector</th>
                <th className="px-3 py-3 text-left whitespace-nowrap">Indexed</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {filteredItems.map((it) => {
                const fileName = it.file_path.split(/[\\/]/).pop() ?? it.file_path
                return (
                  <tr key={it.file_path} className="bg-gray-900 hover:bg-gray-800 transition">
                    <td className="px-3 py-2">
                      {it.has_vector ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={`${STREAM_BASE}/api/thumbnail?path=${encodeURIComponent(it.file_path)}&t=0`}
                          alt=""
                          className="w-16 h-12 object-cover rounded bg-gray-800"
                          loading="lazy"
                          onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = 'hidden' }}
                        />
                      ) : (
                        <div className="w-16 h-12 rounded bg-gray-800" />
                      )}
                    </td>
                    <td className="px-3 py-2 max-w-md">
                      <Link
                        href={`/?q=${encodeURIComponent(`f:${fileName}`)}`}
                        className="text-gray-200 hover:text-blue-300 truncate block transition"
                        title={`Search for "${fileName}" by filename`}
                      >
                        {fileName}
                      </Link>
                      <div className="text-xs text-gray-500 truncate" title={it.file_path}>{it.file_path}</div>
                    </td>
                    <td className="px-3 py-2 text-gray-400">{it.file_type}</td>
                    <td className="px-3 py-2 text-right text-gray-400 font-mono">{formatBytes(it.file_size_bytes)}</td>
                    <td className="px-3 py-2 text-right text-gray-400 font-mono">
                      {it.file_type === 'video' ? formatDuration(it.duration_secs) : '—'}
                    </td>
                    <td className={`px-3 py-2 font-semibold ${STATUS_COLOR[it.processing_status] ?? 'text-gray-400'}`}>
                      {it.processing_status}
                    </td>
                    <td className="px-3 py-2 text-right text-gray-500 font-mono">{it.embedding_ms ?? '—'}</td>
                    <td className="px-3 py-2">
                      {it.has_vector ? (
                        <span className="text-xs px-1.5 py-0.5 rounded bg-green-900/40 text-green-400">✓ indexed</span>
                      ) : (
                        <span className="text-xs px-1.5 py-0.5 rounded bg-yellow-900/40 text-yellow-400">no vector</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-gray-400 whitespace-nowrap" title={it.processed_at ?? undefined}>
                      {relativeTime(it.processed_at)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
