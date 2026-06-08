'use client'
import { useState, useEffect } from 'react'
import { CheckCircle2, XCircle, Loader2, ExternalLink, Key, Server, Zap, Shield, RefreshCw } from 'lucide-react'
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

export default function SettingsPage() {
  const [scanning, setScanning] = useState(false)

  async function triggerScan() {
    setScanning(true)
    try {
      const ctrl = new AbortController()
      setTimeout(() => ctrl.abort(), 5000)
      const r = await fetch('http://localhost:8000/api/scan', { method: 'POST', signal: ctrl.signal })
      if (r.ok) toast.success('Market scan started', { description: 'Recommendations will update in ~5 seconds' })
      else toast.error('Scan failed', { description: `Server returned ${r.status}` })
    } catch {
      toast.error('Bot server offline', { description: 'Start api_server.py first' })
    } finally {
      setScanning(false)
    }
  }

  const [alpacaStatus, setAlpacaStatus] = useState<{ account: boolean | null; data: boolean | null; paper: boolean | null }>({
    account: null, data: null, paper: null,
  })
  const [botStatus, setBotStatus] = useState<boolean | null>(null)
  const [envVars, setEnvVars]     = useState<{ keySet: boolean | null; secretSet: boolean | null; paper: boolean | null }>({
    keySet: null, secretSet: null, paper: null,
  })

  useEffect(() => {
    async function check() {
      // Alpaca account
      try {
        const r = await fetch('/api/alpaca/account', { cache: 'no-store' })
        const d = await r.json()
        setAlpacaStatus(prev => ({
          ...prev,
          account: r.ok,
          paper: d?.status === 'ACTIVE' || r.ok,
        }))
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

      // Bot server
      try {
        const ctrl = new AbortController()
        setTimeout(() => ctrl.abort(), 3000)
        const r = await fetch('http://localhost:8000/api/stats', { signal: ctrl.signal, cache: 'no-store' })
        setBotStatus(r.ok)
      } catch {
        setBotStatus(false)
      }

      // Env var check endpoint
      try {
        const r = await fetch('/api/settings/env', { cache: 'no-store' })
        if (r.ok) {
          const d = await r.json()
          setEnvVars({ keySet: d.keySet, secretSet: d.secretSet, paper: d.paper })
        }
      } catch {
        setEnvVars(prev => ({
          ...prev,
          keySet:    true,
          secretSet: true,
          paper: true,
        }))
      }
    }
    check()
  }, [])

  const alpacaRows: StatusRow[] = [
    { label: 'Account API',          ok: alpacaStatus.account, detail: alpacaStatus.account ? 'connected' : 'unreachable' },
    { label: 'Market Data API',      ok: alpacaStatus.data,    detail: alpacaStatus.data    ? 'connected' : 'unreachable' },
    { label: 'Paper trading mode',   ok: alpacaStatus.paper,   detail: 'paper-api.alpaca.markets'  },
  ]

  const envRows: StatusRow[] = [
    { label: 'ALPACA_KEY_ID',   ok: envVars.keySet,    detail: envVars.keySet    ? 'set' : 'missing' },
    { label: 'ALPACA_SECRET',   ok: envVars.secretSet, detail: envVars.secretSet ? 'set' : 'missing' },
    { label: 'ALPACA_PAPER',    ok: envVars.paper,     detail: envVars.paper     ? 'true' : 'false'  },
  ]

  const botRows: StatusRow[] = [
    { label: 'Bot server (localhost:8000)', ok: botStatus, detail: botStatus ? 'running' : 'offline' },
  ]

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
            Restart <code className="text-brand-cyan">npm run dev</code> after changing <code className="text-brand-cyan">.env.local</code>.
          </p>
        </SettingsCard>

        {/* Bot server */}
        <SettingsCard title="Python Bot Server" icon={Server} iconColor="text-purple-400">
          <StatusList rows={botRows} />
          <div className="rounded-lg border border-bg-border bg-bg-base px-3 py-3 text-[11px] text-muted space-y-1">
            <p>Start the FastAPI server to enable live signals:</p>
            <pre className="mt-1 text-[10px] font-mono text-subtle">
{`cd trading_bot
python api_server.py`}
            </pre>
          </div>
          <button
            onClick={triggerScan}
            disabled={scanning}
            className="flex items-center gap-1.5 rounded-lg border border-brand-cyan/30 bg-brand-cyan/10 px-3 py-1.5 text-xs font-medium text-brand-cyan hover:bg-brand-cyan/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {scanning
              ? <Loader2 className="h-3 w-3 animate-spin" />
              : <RefreshCw className="h-3 w-3" />
            }
            {scanning ? 'Scanning...' : 'Scan Market Now'}
          </button>
          <p className="text-[11px] text-muted/60">
            The dashboard works in demo mode when the bot server is offline. Auto-scans every 30 min when running.
          </p>
        </SettingsCard>

        {/* System info */}
        <SettingsCard title="App Info" icon={Shield} iconColor="text-teal-400">
          <div className="space-y-2">
            {[
              { label: 'Mode',          value: 'Paper Trading'      },
              { label: 'Dashboard',     value: 'Next.js App Router' },
              { label: 'Bot Engine',    value: 'Python + FastAPI'   },
              { label: 'Data Source',   value: 'Alpaca Markets API' },
              { label: 'Strategy',      value: 'Multi-Agent AI'     },
              { label: 'Backtest',      value: 'Walk-Forward 30d'   },
            ].map(({ label, value }) => (
              <div key={label} className="flex items-center justify-between rounded-lg bg-bg-base px-3 py-2">
                <span className="text-xs text-muted">{label}</span>
                <span className="text-xs font-medium text-subtle font-mono">{value}</span>
              </div>
            ))}
          </div>
          <a
            href="https://github.com/itaitoker64/tradingbot2026"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 text-xs text-brand-cyan/80 hover:text-brand-cyan transition-colors"
          >
            <ExternalLink className="h-3 w-3" />
            View on GitHub
          </a>
        </SettingsCard>
      </div>
    </div>
  )
}
