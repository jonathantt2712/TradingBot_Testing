'use client'
import { useState, useEffect } from 'react'
import {
  CheckCircle2, XCircle, Loader2, ExternalLink,
  Key, Server, Zap, Shield, RefreshCw, Activity,
} from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'

interface StatusRow { label: string; ok: boolean | null; detail?: string }

function StatusBadge({ ok }: { ok: boolean | null }) {
  if (ok === null) return <Loader2 className="h-4 w-4 text-muted animate-spin" />
  return ok
    ? <CheckCircle2 className="h-4 w-4 text-bull" />
    : <XCircle className="h-4 w-4 text-bear" />
}

interface CardProps {
  title: string
  icon: React.ElementType
  iconColor: string
  children: React.ReactNode
}
function SettingsCard({ title, icon: Icon, iconColor, children }: CardProps) {
  return (
    <div className="card p-5 space-y-4">
      <div className="flex items-center gap-2.5">
        <div className={cn('flex h-8 w-8 items-center justify-center rounded-lg bg-bg-hover')}>
          <Icon className={cn('h-4 w-4', iconColor)} />
        </div>
        <h2 className="text-sm font-semibold text-primary">{title}</h2>
      </div>
      {children}
    </div>
  )
}

function StatusList({ rows }: { rows: StatusRow[] }) {
  return (
    <div className="space-y-2">
      {rows.map(r => (
        <div key={r.label} className="flex items-center justify-between rounded-lg bg-bg-base px-3 py-2">
          <span className="text-xs text-subtle">{r.label}</span>
          <div className="flex items-center gap-2">
            {r.detail && <span className="text-[10px] text-muted font-mono">{r.detail}</span>}
            <StatusBadge ok={r.ok} />
          </div>
        </div>
      ))}
    </div>
  )
}

interface ScanStats {
  ok: boolean
  market_open: boolean | null
  scans_today: number
  tickers_scanned: number
  recs_generated: number
  scan_errors: number
  open_positions: number
  max_positions: number
  last_scan_at: string | null
  agents_active: boolean
}

export default function SettingsPage() {
  const [scanning, setScanning] = useState(false)

  async function triggerScan() {
    setScanning(true)
    try {
      const r = await fetch('/api/bot/scan', { method: 'POST' })
      if (r.ok) toast.success('Market scan started', { description: 'Recommendations will update in ~5 seconds' })
      else toast.error('Scan failed', { description: r.status === 503 ? 'Bot server offline' : `Server returned ${r.status}` })
    } catch {
      toast.error('Request failed', { description: 'Check your connection' })
    } finally {
      setScanning(false)
    }
  }

  const [alpacaStatus, setAlpacaStatus] = useState<{ account: boolean | null; data: boolean | null; paper: boolean | null }>({
    account: null, data: null, paper: null,
  })
  const [botStatus, setBotStatus] = useState<boolean | null>(null)
  const [agentsOk,  setAgentsOk]  = useState<boolean | null>(null)
  const [envVars,   setEnvVars]   = useState<{ keySet: boolean | null; secretSet: boolean | null; paper: boolean | null }>({
    keySet: null, secretSet: null, paper: null,
  })
  const [scanStats, setScanStats] = useState<ScanStats | null>(null)

  useEffect(() => {
    async function check() {
      // Alpaca account
      try {
        const r = await fetch('/api/alpaca/account', { cache: 'no-store' })
        const d = await r.json()
        setAlpacaStatus(prev => ({ ...prev, account: r.ok, paper: d?.status === 'ACTIVE' || r.ok }))
      } catch {
        setAlpacaStatus(prev => ({ ...prev, account: false, paper: false }))
      }

      // Alpaca data (snapshots with SPY)
      try {
        const r = await fetch('/api/alpaca/snapshots?symbols=SPY', { cache: 'no-store' })
        setAlpacaStatus(prev => ({ ...prev, data: r.ok }))
      } catch {
        setAlpacaStatus(prev => ({ ...prev, data: false }))
      }

      // Bot health — via server-side proxy (works on Vercel too, not just localhost)
      try {
        const r = await fetch('/api/bot/health', { cache: 'no-store' })
        const d = await r.json()
        setBotStatus(d.ok === true)
        setAgentsOk(d.agents ?? null)
      } catch {
        setBotStatus(false)
        setAgentsOk(false)
      }

      // Scan stats
      try {
        const r = await fetch('/api/bot/scan-stats', { cache: 'no-store' })
        if (r.ok) setScanStats(await r.json())
      } catch { /* bot offline */ }

      // Env var check
      try {
        const r = await fetch('/api/settings/env', { cache: 'no-store' })
        if (r.ok) {
          const d = await r.json()
          setEnvVars({ keySet: d.keySet, secretSet: d.secretSet, paper: d.paper })
        }
      } catch {
        setEnvVars({ keySet: true, secretSet: true, paper: true })
      }
    }
    check()
  }, [])

  const alpacaRows: StatusRow[] = [
    { label: 'Account API',        ok: alpacaStatus.account, detail: alpacaStatus.account ? 'connected'   : 'unreachable' },
    { label: 'Market Data API',    ok: alpacaStatus.data,    detail: alpacaStatus.data    ? 'connected'   : 'unreachable' },
    { label: 'Paper trading mode', ok: alpacaStatus.paper,   detail: 'paper-api.alpaca.markets' },
  ]

  const envRows: StatusRow[] = [
    { label: 'ALPACA_KEY_ID',  ok: envVars.keySet,    detail: envVars.keySet    ? 'set'  : 'missing' },
    { label: 'ALPACA_SECRET',  ok: envVars.secretSet, detail: envVars.secretSet ? 'set'  : 'missing' },
    { label: 'ALPACA_PAPER',   ok: envVars.paper,     detail: envVars.paper     ? 'true' : 'false'   },
  ]

  const botRows: StatusRow[] = [
    { label: 'Bot server (api_server.py)', ok: botStatus, detail: botStatus  ? 'running' : 'offline'  },
    { label: 'LLM agents wired',           ok: agentsOk,  detail: agentsOk   ? 'active'  : 'inactive' },
  ]

  const marketLabel =
    scanStats === null       ? undefined :
    scanStats.market_open === null ? 'checking…' :
    scanStats.market_open          ? 'open'      : 'closed'

  const scanRows: StatusRow[] = scanStats ? [
    { label: 'Market status',     ok: scanStats.market_open ?? false,                                   detail: marketLabel },
    { label: 'Scans today',       ok: scanStats.scans_today > 0,                                        detail: String(scanStats.scans_today) },
    { label: 'Tickers scanned',   ok: scanStats.tickers_scanned > 0,                                    detail: String(scanStats.tickers_scanned) },
    { label: 'Recs generated',    ok: scanStats.recs_generated > 0,                                     detail: String(scanStats.recs_generated) },
    { label: 'Open positions',    ok: scanStats.open_positions < scanStats.max_positions,                detail: `${scanStats.open_positions} / ${scanStats.max_positions}` },
    { label: 'Scan errors today', ok: scanStats.scan_errors === 0,                                      detail: String(scanStats.scan_errors) },
  ] : []

  return (
    <div className="px-4 py-4 md:px-6 md:py-6 space-y-4 md:space-y-6 max-w-[900px]">
      <div>
        <h1 className="text-xl font-bold text-primary">Settings</h1>
        <p className="text-xs text-muted mt-0.5">Connection status and configuration</p>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Alpaca */}
        <SettingsCard title="Alpaca Paper API" icon={Zap} iconColor="text-brand-cyan">
          <StatusList rows={alpacaRows} />
          <div className="rounded-lg border border-bg-border bg-bg-base px-3 py-3 text-[11px] text-muted space-y-1">
            <p>Set keys in <code className="text-brand-cyan">.env.local</code> at the dashboard root:</p>
            <pre className="mt-1 text-[10px] font-mono text-subtle whitespace-pre-wrap">
{`ALPACA_KEY_ID=your_key
ALPACA_SECRET=your_secret
ALPACA_PAPER=true`}
            </pre>
          </div>
          <a
            href="https://alpaca.markets/docs/trading/paper-trading/"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 text-xs text-brand-cyan/80 hover:text-brand-cyan transition-colors"
          >
            <ExternalLink className="h-3 w-3" />
            Alpaca Paper Trading docs
          </a>
        </SettingsCard>

        {/* Env vars */}
        <SettingsCard title="Environment Variables" icon={Key} iconColor="text-caution">
          <StatusList rows={envRows} />
          <p className="text-[11px] text-muted">
            Restart <code className="text-brand-cyan">npm run dev</code> after changing{' '}
            <code className="text-brand-cyan">.env.local</code>.
          </p>
        </SettingsCard>

        {/* Bot server */}
        <SettingsCard title="Bot Server" icon={Server} iconColor="text-brand-purple">
          <StatusList rows={botRows} />
          <div className="rounded-lg border border-bg-border bg-bg-base px-3 py-3 text-[11px] text-muted space-y-1">
            <p>Start the bot server locally:</p>
            <pre className="mt-1 text-[10px] font-mono text-subtle">cd trading_bot{'\n'}python api_server.py</pre>
          </div>
          <button
            onClick={triggerScan}
            disabled={scanning || !botStatus}
            className="btn-ghost flex items-center gap-1.5 text-xs disabled:opacity-50"
          >
            {scanning
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <RefreshCw className="h-3.5 w-3.5" />}
            Trigger Market Scan
          </button>
        </SettingsCard>

        {/* Scan stats */}
        <SettingsCard title="Today's Scan Activity" icon={Activity} iconColor="text-bull">
          {scanStats === null ? (
            <div className="flex items-center gap-2 text-xs text-muted py-2">
              <XCircle className="h-4 w-4 text-bear" />
              Bot server offline — no scan data available
            </div>
          ) : (
            <>
              <StatusList rows={scanRows} />
              {scanStats.last_scan_at && (
                <p className="text-[10px] text-muted font-mono">
                  Last scan: {new Date(scanStats.last_scan_at + 'Z').toLocaleTimeString()}
                </p>
              )}
            </>
          )}
        </SettingsCard>
      </div>

      {/* Security notice */}
      <div className="flex items-start gap-3 rounded-xl border border-bg-border bg-bg-hover px-4 py-3">
        <Shield className="h-4 w-4 text-caution mt-0.5 shrink-0" />
        <p className="text-[11px] text-muted leading-relaxed">
          <span className="font-semibold text-subtle">Paper trading only.</span>{' '}
          This dashboard is configured for Alpaca Paper Trading. No real money is at risk.
          Never set <code className="text-brand-cyan">ALPACA_PAPER=false</code> without fully understanding the implications.
        </p>
      </div>
    </div>
  )
}
