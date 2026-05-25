'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'

interface RecoveryPlanStep {
  group: 'stuck' | 'retryable' | 'eio'
  action: 'reset_pending' | 'operator_required'
  count: number
  rationale: string
}

interface RecoveryResult {
  scan_id: string
  dry_run: boolean
  eio_count: number
  stuck_count: number
  retryable_count: number
  recovered_count: number
  skipped_count: number
  failed_count: number
  tasks_dispatched: number
  investigation: string
  recovery_plan: RecoveryPlanStep[]
  summary: string
  execution_errors: string[]
}

interface HistoryEntry {
  id: string
  timestamp: string
  scan_id: string
  dry_run: boolean
  recovered: number
  skipped: number
  failed: number
  dispatched: number
}

const GROUP_LABEL: Record<string, string> = {
  stuck:     'Stuck (>2h)',
  retryable: 'Retryable errors',
  eio:       'EIO (SMB)',
}

const GROUP_COLOR: Record<string, string> = {
  stuck:     'text-yellow-400',
  retryable: 'text-blue-400',
  eio:       'text-orange-400',
}

const GROUP_BG: Record<string, string> = {
  stuck:     'bg-yellow-900/30 border-yellow-700',
  retryable: 'bg-blue-900/30 border-blue-700',
  eio:       'bg-orange-900/30 border-orange-700',
}

function ActionBadge({ action }: { action: string }) {
  if (action === 'reset_pending') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-green-900 text-green-300 border border-green-700">
        ↺ Reset + requeue
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-orange-900 text-orange-300 border border-orange-700">
      ⚠ Operator required
    </span>
  )
}

function CountCard({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-4 text-center">
      <div className="text-xs text-gray-400 uppercase tracking-wide mb-1">{label}</div>
      <div className={`text-3xl font-bold ${color}`}>{value}</div>
    </div>
  )
}

function Spinner() {
  return <div className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin inline-block" />
}

export default function RecoveryPage() {
  const [result, setResult] = useState<RecoveryResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [history, setHistory] = useState<HistoryEntry[]>([])

  useEffect(() => {
    fetch('/api/recovery/history')
      .then(r => r.ok ? r.json() : [])
      .then(setHistory)
      .catch(() => {})
  }, [result]) // refetch after each scan so history stays current

  async function runScan(dryRun: boolean) {
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const resp = await fetch('/api/recovery', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dry_run: dryRun }),
      })
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}))
        throw new Error(err.detail || `Server error ${resp.status}`)
      }
      setResult(await resp.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  const hasIssues = result && (result.eio_count + result.stuck_count + result.retryable_count) > 0

  return (
    <div className="max-w-4xl mx-auto px-6 py-8">

      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">Worker Recovery</h1>
          <p className="text-sm text-gray-400 mt-1">
            Detect and remediate stuck or errored media files
          </p>
        </div>
        <Link href="/" className="text-sm text-blue-400 hover:text-blue-300 transition">
          ← Back to search
        </Link>
      </div>

      {/* Action buttons */}
      <div className="flex gap-3 mb-8">
        <button
          onClick={() => runScan(true)}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 rounded-lg border border-gray-600 bg-gray-800 text-gray-300 hover:bg-gray-700 hover:text-white transition text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {loading ? <Spinner /> : <span>🔍</span>}
          Preview (dry run)
        </button>
        <button
          onClick={() => runScan(false)}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white transition text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {loading ? <Spinner /> : <span>⚡</span>}
          Run Recovery
        </button>
      </div>

      {/* Loading state */}
      {loading && (
        <div className="flex items-center gap-3 text-gray-400 py-12 justify-center">
          <div className="w-5 h-5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          Running recovery scan…
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="p-4 rounded-lg bg-red-900/40 border border-red-700 text-red-300 text-sm mb-6">
          {error}
        </div>
      )}

      {/* Results */}
      {result && !loading && (
        <div className="space-y-6">

          {/* Mode badge + scan ID */}
          <div className="flex items-center gap-3">
            {result.dry_run ? (
              <span className="px-3 py-1 rounded-full text-xs font-semibold bg-gray-700 text-gray-300 border border-gray-600">
                DRY RUN — no changes made
              </span>
            ) : (
              <span className="px-3 py-1 rounded-full text-xs font-semibold bg-green-900 text-green-300 border border-green-700">
                LIVE RUN
              </span>
            )}
            <span className="text-xs text-gray-500">scan {result.scan_id}</span>
          </div>

          {/* Detected counts */}
          <div>
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">
              Detected
            </h2>
            <div className="grid grid-cols-3 gap-3">
              <CountCard label="Stuck (>2h)"     value={result.stuck_count}     color="text-yellow-400" />
              <CountCard label="Retryable errors" value={result.retryable_count} color="text-blue-400" />
              <CountCard label="EIO (SMB)"        value={result.eio_count}       color="text-orange-400" />
            </div>
          </div>

          {/* No issues */}
          {!hasIssues && (
            <div className="p-4 rounded-lg bg-green-900/20 border border-green-800 text-green-400 text-sm text-center">
              ✓ Pipeline clean — no errors or stuck tasks detected
            </div>
          )}

          {/* Investigation */}
          {result.investigation && (
            <div>
              <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-2">
                Assessment
              </h2>
              <div className="p-4 rounded-lg bg-gray-800 border border-gray-700 text-sm text-gray-300 leading-relaxed">
                {result.investigation}
              </div>
            </div>
          )}

          {/* Recovery plan */}
          {result.recovery_plan.length > 0 && (
            <div>
              <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">
                Recovery plan
              </h2>
              <div className="rounded-lg border border-gray-700 overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-gray-800 text-gray-400 text-xs uppercase tracking-wide">
                    <tr>
                      <th className="px-4 py-3 text-left">Group</th>
                      <th className="px-4 py-3 text-left">Action</th>
                      <th className="px-4 py-3 text-right">Files</th>
                      <th className="px-4 py-3 text-left">Rationale</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-700">
                    {result.recovery_plan.map((step, i) => (
                      <tr key={i} className={`${GROUP_BG[step.group]} hover:brightness-110 transition`}>
                        <td className={`px-4 py-3 font-medium ${GROUP_COLOR[step.group]}`}>
                          {GROUP_LABEL[step.group] ?? step.group}
                        </td>
                        <td className="px-4 py-3">
                          <ActionBadge action={step.action} />
                        </td>
                        <td className={`px-4 py-3 text-right font-mono font-semibold ${GROUP_COLOR[step.group]}`}>
                          {step.count}
                        </td>
                        <td className="px-4 py-3 text-gray-400 text-xs">{step.rationale}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Execution results (live run only) */}
          {!result.dry_run && (
            <div>
              <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">
                Execution results
              </h2>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <CountCard label="Recovered"  value={result.recovered_count}  color="text-green-400" />
                <CountCard label="Dispatched" value={result.tasks_dispatched} color="text-blue-400" />
                <CountCard label="Skipped"    value={result.skipped_count}    color="text-gray-400" />
                <CountCard label="Failed"     value={result.failed_count}     color="text-red-400" />
              </div>
            </div>
          )}

          {/* Dry run would-recover summary */}
          {result.dry_run && result.recovered_count > 0 && (
            <div className="p-4 rounded-lg bg-blue-900/20 border border-blue-800 text-blue-300 text-sm">
              Would recover <span className="font-bold">{result.recovered_count}</span> file
              {result.recovered_count !== 1 ? 's' : ''} and dispatch{' '}
              <span className="font-bold">{result.recovered_count}</span> task
              {result.recovered_count !== 1 ? 's' : ''}.
              Click <strong>Run Recovery</strong> to apply.
            </div>
          )}

          {/* Execution errors */}
          {result.execution_errors.length > 0 && (
            <div>
              <h2 className="text-sm font-semibold text-red-400 uppercase tracking-wide mb-2">
                Execution errors ({result.execution_errors.length})
              </h2>
              <div className="rounded-lg border border-red-800 bg-red-900/20 p-4 space-y-1">
                {result.execution_errors.map((e, i) => (
                  <p key={i} className="text-xs text-red-300 font-mono">{e}</p>
                ))}
              </div>
            </div>
          )}

          {/* EIO operator notice */}
          {result.eio_count > 0 && (
            <div className="p-4 rounded-lg bg-orange-900/20 border border-orange-700 text-sm text-orange-300">
              <span className="font-semibold">⚠ Operator action required for {result.eio_count} EIO error{result.eio_count !== 1 ? 's' : ''}.</span>{' '}
              The SMB share dropped mid-transfer. Remount the share, then re-run recovery.
            </div>
          )}

        </div>
      )}

      {/* Recovery history */}
      <div className="mt-10">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">
          Recovery history
        </h2>
        {history.length === 0 ? (
          <p className="text-xs text-gray-500">No live recovery runs recorded yet.</p>
        ) : (
          <div className="rounded-lg border border-gray-700 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-800 text-gray-400 text-xs uppercase tracking-wide">
                <tr>
                  <th className="px-4 py-3 text-left">Timestamp</th>
                  <th className="px-4 py-3 text-left">Scan ID</th>
                  <th className="px-4 py-3 text-right">Recovered</th>
                  <th className="px-4 py-3 text-right">Dispatched</th>
                  <th className="px-4 py-3 text-right">Skipped</th>
                  <th className="px-4 py-3 text-right">Failed</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700">
                {history.map(entry => (
                  <tr key={entry.id} className="bg-gray-900 hover:bg-gray-800 transition">
                    <td className="px-4 py-3 text-gray-400 text-xs font-mono whitespace-nowrap">
                      {new Date(entry.timestamp).toLocaleString()}
                    </td>
                    <td className="px-4 py-3 text-gray-500 text-xs font-mono">
                      {entry.scan_id}
                    </td>
                    <td className="px-4 py-3 text-right font-mono font-semibold text-green-400">
                      {entry.recovered}
                    </td>
                    <td className="px-4 py-3 text-right font-mono font-semibold text-blue-400">
                      {entry.dispatched}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-gray-400">
                      {entry.skipped}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-red-400">
                      {entry.failed}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

    </div>
  )
}
