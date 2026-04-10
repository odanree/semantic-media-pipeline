'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'

interface QueryStat {
  query: string
  positives: number
  negatives: number
  ratio: number
  tier: 'best' | 'great' | 'good' | 'needs_data'
}

interface Totals {
  queries: number
  positives: number
  negatives: number
  ratio: number
}

interface Goals {
  queries: { good: number; great: number; best: number }
  positives: { good: number; great: number; best: number }
  negatives: { good: number; great: number; best: number }
  ratio: { good: number; great: number; best: number }
}

interface TrainingData {
  queries: QueryStat[]
  totals: Totals
  tiers: Record<string, string>
  goals: Goals
}

const TIER_COLOR: Record<string, string> = {
  best:       'text-green-400',
  great:      'text-blue-400',
  good:       'text-yellow-400',
  needs_data: 'text-red-400',
}

const TIER_BG: Record<string, string> = {
  best:       'bg-green-900 border-green-700',
  great:      'bg-blue-900 border-blue-700',
  good:       'bg-yellow-900 border-yellow-700',
  needs_data: 'bg-red-900 border-red-800',
}

const TIER_LABEL: Record<string, string> = {
  best:       'Best',
  great:      'Great',
  good:       'Good',
  needs_data: 'Needs data',
}

function ProgressBar({ value, good, great, best }: { value: number; good: number; great: number; best: number }) {
  const pct = Math.min(100, (value / best) * 100)
  const color = value >= best ? 'bg-green-500' : value >= great ? 'bg-blue-500' : value >= good ? 'bg-yellow-500' : 'bg-red-500'
  return (
    <div className="relative h-2 bg-gray-700 rounded-full overflow-hidden">
      <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
      {/* Goal markers */}
      {[good, great].map((g, i) => (
        <div key={i} className="absolute top-0 h-full w-px bg-gray-500 opacity-60" style={{ left: `${(g / best) * 100}%` }} />
      ))}
    </div>
  )
}

function StatCard({ label, value, tier, good, great, best, format = 'number' }: {
  label: string; value: number; tier: string
  good: number; great: number; best: number; format?: 'number' | 'ratio'
}) {
  const display = format === 'ratio' ? `${(value * 100).toFixed(0)}%` : value.toLocaleString()
  return (
    <div className={`rounded-lg border p-4 ${TIER_BG[tier]}`}>
      <div className="text-xs text-gray-400 uppercase tracking-wide mb-1">{label}</div>
      <div className={`text-2xl font-bold ${TIER_COLOR[tier]}`}>{display}</div>
      <div className={`text-xs font-semibold mt-0.5 ${TIER_COLOR[tier]}`}>{TIER_LABEL[tier]}</div>
      <div className="mt-2">
        <ProgressBar value={value} good={good} great={great} best={best} />
        <div className="flex justify-between text-xs text-gray-500 mt-1">
          <span>Good: {format === 'ratio' ? `${(good * 100).toFixed(0)}%` : good.toLocaleString()}</span>
          <span>Great: {format === 'ratio' ? `${(great * 100).toFixed(0)}%` : great.toLocaleString()}</span>
          <span>Best: {format === 'ratio' ? `${(best * 100).toFixed(0)}%` : best.toLocaleString()}</span>
        </div>
      </div>
    </div>
  )
}

export default function TrainingPage() {
  const [data, setData] = useState<TrainingData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/training')
      .then(r => r.ok ? r.json() : Promise.reject('Failed'))
      .then(setData)
      .catch(() => setError('Could not load training stats'))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">Re-ranker Training Readiness</h1>
          <p className="text-sm text-gray-400 mt-1">Vote data quality for training a learned re-ranking model</p>
        </div>
        <Link href="/" className="text-sm text-blue-400 hover:text-blue-300 transition">← Back to search</Link>
      </div>

      {loading && (
        <div className="flex items-center gap-3 text-gray-400 py-12 justify-center">
          <div className="w-5 h-5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          Loading stats…
        </div>
      )}

      {error && <div className="text-red-400 text-sm py-8 text-center">{error}</div>}

      {data && (
        <>
          {/* Overall score cards */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
            <StatCard label="Distinct queries" value={data.totals.queries}
              tier={data.tiers.queries} {...data.goals.queries} />
            <StatCard label="Positive votes" value={data.totals.positives}
              tier={data.tiers.positives} {...data.goals.positives} />
            <StatCard label="Negative votes" value={data.totals.negatives}
              tier={data.tiers.negatives} {...data.goals.negatives} />
            <StatCard label="Neg / Pos ratio" value={data.totals.ratio}
              tier={data.tiers.ratio} {...data.goals.ratio} format="ratio" />
          </div>

          {/* Context */}
          <div className="mb-6 p-4 bg-gray-800 rounded-lg border border-gray-700 text-sm text-gray-400 space-y-1">
            <p><span className="text-white font-medium">Distinct queries</span> — more unique search terms = better generalization to new queries</p>
            <p><span className="text-white font-medium">Positives</span> — manual upvotes with a keyword label (training signal)</p>
            <p><span className="text-white font-medium">Negatives</span> — thumbs down votes (needed to learn the decision boundary)</p>
            <p><span className="text-white font-medium">Neg/Pos ratio</span> — ideally 1:1; below 50% the model may score everything high</p>
          </div>

          {/* Per-query table */}
          <h2 className="text-lg font-semibold text-white mb-3">Per-query breakdown</h2>
          <div className="rounded-lg border border-gray-700 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-800 text-gray-400 text-xs uppercase tracking-wide">
                <tr>
                  <th className="px-4 py-3 text-left">Query</th>
                  <th className="px-4 py-3 text-right">Positives</th>
                  <th className="px-4 py-3 text-right">Negatives</th>
                  <th className="px-4 py-3 text-right">Neg/Pos</th>
                  <th className="px-4 py-3 text-right">Tier</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700">
                {data.queries.map(q => (
                  <tr key={q.query} className="bg-gray-900 hover:bg-gray-800 transition">
                    <td className="px-4 py-3 font-medium">
                      <Link
                        href={`/?q=${encodeURIComponent(q.query)}`}
                        className="text-white hover:text-blue-400 transition"
                        title={`Search for "${q.query}"`}
                      >
                        {q.query}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-right text-green-400">{q.positives.toLocaleString()}</td>
                    <td className="px-4 py-3 text-right text-red-400">{q.negatives.toLocaleString()}</td>
                    <td className="px-4 py-3 text-right text-gray-300">{(q.ratio * 100).toFixed(0)}%</td>
                    <td className="px-4 py-3 text-right">
                      <span className={`text-xs font-semibold ${TIER_COLOR[q.tier]}`}>{TIER_LABEL[q.tier]}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
