export const AGENT_ORDER = ['technical', 'fundamental', 'vision', 'risk', 'social', 'liquid'] as const

export const AGENT_LABELS: Record<string, string> = {
  technical:   'Technical',
  fundamental: 'Fundamental',
  vision:      'Vision (Chart)',
  risk:        'Risk',
  social:      'Social Sentiment',
  liquid:      'Liquidity Flow',
}

export const AGENT_BLURBS: Record<string, string> = {
  technical:   'Price action, VWAP, relative strength & volume',
  fundamental: 'News & earnings keyword signals',
  vision:      'Chart pattern recognition',
  risk:        'Position sizing, stop placement & R/R viability',
  social:      'Community / social sentiment chatter',
  liquid:      'Order flow & liquidity dynamics',
}
