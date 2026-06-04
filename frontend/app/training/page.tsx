'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'

interface QueryStat {
  query: string
  positives: number
  positives_direct: number
  positives_cascade: number
  negatives: number
  negatives_direct: number
  negatives_cascade: number
  ratio: number
  tier: 'best' | 'great' | 'good' | 'needs_data'
  tier_direct: 'best' | 'great' | 'good' | 'needs_data'
}

interface Totals {
  queries: number
  positives: number
  positives_direct: number
  positives_cascade: number
  negatives: number
  negatives_direct: number
  negatives_cascade: number
  ratio: number
  ratio_direct: number
}

interface Goals {
  queries:   { good: number; great: number; best: number }
  positives: { good: number; great: number; best: number }
  negatives: { good: number; great: number; best: number }
  ratio:     { good: number; great: number; best: number }
}

interface TrainingData {
  queries: QueryStat[]
  totals: Totals
  tiers: Record<string, string>
  goals: Goals
}

interface EvalQueryStat {
  query: string
  positives: number
  negatives: number
  total: number
}

interface EvalData {
  queries: EvalQueryStat[]
  totals: { queries: number; positives: number; negatives: number; total: number }
  tiers:  Record<string, string>
  goals:  { queries: { good: number; great: number; best: number }
           positives: { good: number; great: number; best: number }
           negatives: { good: number; great: number; best: number } }
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
      {[good, great].map((g, i) => (
        <div key={i} className="absolute top-0 h-full w-px bg-gray-500 opacity-60" style={{ left: `${(g / best) * 100}%` }} />
      ))}
    </div>
  )
}

function StatCard({ label, value, tier, good, great, best, format = 'number', sub }: {
  label: string; value: number; tier: string
  good: number; great: number; best: number; format?: 'number' | 'ratio'
  sub?: { label: string; value: number; tier: string; format?: 'number' | 'ratio' }
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
      {sub && (
        <div className="mt-3 pt-2 border-t border-gray-700/50 text-xs">
          <div className="flex items-center justify-between">
            <span className="text-gray-400">{sub.label}</span>
            <span className={`font-semibold ${TIER_COLOR[sub.tier]}`}>
              {sub.format === 'ratio' ? `${(sub.value * 100).toFixed(0)}%` : sub.value.toLocaleString()}
              <span className="ml-1.5 opacity-70">({TIER_LABEL[sub.tier]})</span>
            </span>
          </div>
        </div>
      )}
    </div>
  )
}

export default function TrainingPage() {
  const [data, setData] = useState<TrainingData | null>(null)
  const [evalData, setEvalData] = useState<EvalData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([
      fetch('/api/training').then(r => r.ok ? r.json() : Promise.reject('Failed')),
      // Eval-set readiness is best-effort — show null on failure so the main panel still renders.
      fetch('/api/eval-set/readiness').then(r => r.ok ? r.json() : null).catch(() => null),
    ])
      .then(([t, e]) => { setData(t); setEvalData(e) })
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
          {/* Overall score cards — main value is total (direct + cascade);
              the sub-row inside each card is the direct-only counterpart,
              which is the honest "do we have human labels" signal. */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
            <StatCard label="Distinct queries" value={data.totals.queries}
              tier={data.tiers.queries} {...data.goals.queries} />
            <StatCard label="Positive votes" value={data.totals.positives}
              tier={data.tiers.positives} {...data.goals.positives}
              sub={{
                label: 'Direct (human) only',
                value: data.totals.positives_direct,
                tier:  data.tiers.positives_direct,
              }}
            />
            <StatCard label="Negative votes" value={data.totals.negatives}
              tier={data.tiers.negatives} {...data.goals.negatives}
              sub={{
                label: 'Direct (human) only',
                value: data.totals.negatives_direct,
                tier:  data.tiers.negatives_direct,
              }}
            />
            <StatCard label="Neg / Pos ratio" value={data.totals.ratio}
              tier={data.tiers.ratio} {...data.goals.ratio} format="ratio"
              sub={{
                label:  'Direct-only ratio',
                value:  data.totals.ratio_direct,
                tier:   data.tiers.ratio_direct,
                format: 'ratio',
              }}
            />
          </div>

          {/* Cascade share callout — surfaces how much of the green tier above is
              actually cascade-derived. Hidden when there are no positives at all. */}
          {data.totals.positives > 0 && (
            <div className="mb-8 p-3 rounded-lg border border-amber-800/50 bg-amber-950/30 text-sm text-amber-200/90" data-testid="cascade-share">
              <span className="font-medium text-amber-200">Label provenance:</span>{' '}
              {data.totals.positives_direct.toLocaleString()} direct human positives
              {' + '}
              {data.totals.positives_cascade.toLocaleString()} cascade-propagated
              {' '}({Math.round((data.totals.positives_cascade / data.totals.positives) * 100)}% cascade).
              Direct labels are the trustworthy training signal — fine-tune readiness should be judged on the direct numbers, not the totals.
            </div>
          )}

          {/* Context */}
          <div className="mb-6 p-4 bg-gray-800 rounded-lg border border-gray-700 text-sm text-gray-400 space-y-1">
            <p><span className="text-white font-medium">Distinct queries</span> — more unique search terms = better generalization to new queries</p>
            <p><span className="text-white font-medium">Positives</span> — manual upvotes with a keyword label (training signal)</p>
            <p><span className="text-white font-medium">Negatives</span> — thumbs down votes (needed to learn the decision boundary)</p>
            <p><span className="text-white font-medium">Neg/Pos ratio</span> — ideally 1:1; below 50% the model may score everything high</p>
            <p className="pt-1"><span className="text-amber-300 font-medium">Direct vs cascade</span> — cascade-propagated votes inflate volume but share their label source; direct labels are independent samples and matter more for generalization</p>
          </div>

          {/* Per-query table */}
          <h2 className="text-lg font-semibold text-white mb-3">Per-query breakdown</h2>
          <div className="rounded-lg border border-gray-700 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-800 text-gray-400 text-xs uppercase tracking-wide">
                <tr>
                  <th className="px-4 py-3 text-left">Query</th>
                  <th className="px-4 py-3 text-right">Positives (direct / cascade)</th>
                  <th className="px-4 py-3 text-right">Negatives</th>
                  <th className="px-4 py-3 text-right">Neg/Pos</th>
                  <th className="px-4 py-3 text-right">Tier (direct)</th>
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
                    <td className="px-4 py-3 text-right">
                      <span className="text-green-400 font-semibold">{q.positives_direct.toLocaleString()}</span>
                      <span className="text-gray-500 mx-1">/</span>
                      <span className="text-teal-400">{q.positives_cascade.toLocaleString()}</span>
                    </td>
                    <td className="px-4 py-3 text-right text-red-400">{q.negatives.toLocaleString()}</td>
                    <td className="px-4 py-3 text-right text-gray-300">{(q.ratio * 100).toFixed(0)}%</td>
                    <td className="px-4 py-3 text-right">
                      <span className={`text-xs font-semibold ${TIER_COLOR[q.tier_direct]}`}>{TIER_LABEL[q.tier_direct]}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Held-out eval set readiness — separate, smaller goals, curated. */}
          <h2 className="text-lg font-semibold text-white mt-10 mb-3" data-testid="eval-section-heading">
            Held-out evaluation set
          </h2>
          <p className="text-sm text-gray-400 mb-4">
            Curated labels kept separate from the vote loop, used to measure regressions when a re-ranker is trained.
            Smaller goals than training because this set is meant to be hand-picked.
          </p>
          {evalData ? (
            <div className="grid grid-cols-3 gap-4" data-testid="eval-stat-grid">
              <StatCard label="Distinct queries" value={evalData.totals.queries}
                tier={evalData.tiers.queries} {...evalData.goals.queries} />
              <StatCard label="Positives" value={evalData.totals.positives}
                tier={evalData.tiers.positives} {...evalData.goals.positives} />
              <StatCard label="Negatives" value={evalData.totals.negatives}
                tier={evalData.tiers.negatives} {...evalData.goals.negatives} />
            </div>
          ) : (
            <div className="p-4 rounded-lg border border-gray-700 bg-gray-800/50 text-sm text-gray-400">
              Eval-set stats unavailable.
            </div>
          )}
        </>
      )}
    </div>
  )
}
