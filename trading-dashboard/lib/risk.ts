/**
 * Mirrors RiskAgent.build_plan's sizing formula
 * (trading_bot/agents/risk_agent.py) — sizes a position to the EXECUTING
 * USER's own account equity, not the bot's.
 */
const RISK_PER_TRADE_PCT = Number(process.env.RISK_PER_TRADE_PCT ?? '0.01')
const MAX_POSITION_PCT   = Number(process.env.MAX_POSITION_PCT   ?? '0.20')

export function sizePosition(equity: number, entry: number, stopLoss: number): number {
  const perShareRisk = Math.abs(entry - stopLoss)
  if (perShareRisk <= 0 || entry <= 0 || equity <= 0) return 0

  const riskUsd       = equity * RISK_PER_TRADE_PCT
  const qtyByRisk     = riskUsd / perShareRisk
  const qtyByExposure = (equity * MAX_POSITION_PCT) / entry

  return Math.floor(Math.min(qtyByRisk, qtyByExposure))
}
