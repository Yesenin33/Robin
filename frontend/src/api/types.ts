// Mirrors backend/app/schemas/*
// UNKNOWN = we could not check it (data was unavailable): shown grey, never as safe
export type Verdict = 'CLEAN' | 'RISKY' | 'DANGER' | 'UNKNOWN'
export type Tone = 'ok' | 'warn' | 'bad' | 'unknown'

export interface Wallet { address: string; kind: string }
export interface User { id: number; plan: string; wallets: Wallet[] }
export interface TokenOut { access_token: string; token_type: string; user: User }

export interface Holding {
  token: string
  symbol: string
  name: string | null
  amount: number
  price_usd: number | null
  value_usd: number
  change_24h_pct: number | null
  pnl_pct: number | null
  verdict: Verdict | null
}
export interface Portfolio {
  address: string
  total_usd: number
  change_24h_usd: number
  change_24h_pct: number
  holdings: Holding[]
  updated_at: string
}

export interface Metric { key: string; label: string; emoji: string; value: string; tone: Tone; hint: string }
export interface Safety {
  address: string
  symbol: string | null
  name: string | null
  score: number | null  // null when verdict is UNKNOWN
  verdict: Verdict
  swarm_verdict: string | null
  summary: string
  risks: string[]
  metrics: Metric[]
  buy_recommended: boolean
  price_usd: number | null
  scanned_at: string
  holders_stale_at?: string | null  // holder data is the last good snapshot (explorer was busy)
  stale?: boolean  // a requested fresh scan failed; this is the previous result
}
export interface ScanQueued { address: string; queued: true }

export type RiskTag = 'low' | 'medium' | 'high'

export interface CopyItem {
  address: string
  ts: string
  side: 'buy' | 'sell'
  token_symbol: string
  token_address: string | null
  tx_hash: string | null
  amount_usd: number | null
  reason: string
  risk_tag: RiskTag
  verdict: Verdict | null
  following: boolean
}

export interface Suggested {
  address: string
  realised_pnl_usd: number
  wins: number
  losses: number
  tokens: number
  early_buys: number
  reason: string
  risk_tag: RiskTag
  following: boolean
}

export interface AlertItem {
  id: number
  kind: string
  title: string
  body: string
  url: string | null
  created_at: string
  read: boolean
}
export interface Alerts { unread: number; items: AlertItem[] }
export interface Channels { push: boolean; telegram: boolean; telegram_alerts?: boolean; telegram_bot: string | null }
