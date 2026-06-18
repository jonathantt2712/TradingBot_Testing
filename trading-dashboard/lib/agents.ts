export const AGENT_ORDER = ['technical', 'fundamental', 'vision', 'risk', 'social', 'liquid', 'insider', 'squeeze', 'macro'] as const

export const AGENT_LABELS: Record<string, string> = {
  technical:   'Technical',
  fundamental: 'Fundamental',
  vision:      'Vision (Chart)',
  risk:        'Risk',
  social:      'Social Sentiment',
  liquid:      'Liquidity Flow',
  insider:     'Congressional Intel',
  squeeze:     'Short Squeeze',
  macro:       'Macro Signal',
}

export const AGENT_BLURBS: Record<string, string> = {
  technical:   'Price action, VWAP, relative strength & volume',
  fundamental: 'News & earnings keyword signals',
  vision:      'Chart pattern recognition',
  risk:        'Position sizing, stop placement & R/R viability',
  social:      'Community / social sentiment chatter',
  liquid:      'Order flow & liquidity dynamics',
  insider:     'Congressional trading disclosure signals (House Stock Watcher)',
  squeeze:     'FINRA daily short volume ratio — detects squeeze setups and short-covering signals',
  macro:       'BTC 7d + QQQ 20d + QQQ vs XLP rotation + safe-haven pressure (GLD/UUP) — AI-Trader macro signals via Yahoo Finance',
}
