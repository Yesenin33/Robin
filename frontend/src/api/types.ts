// Mirrors backend/app/schemas/*
export type Verdict = 'CLEAN' | 'RISKY' | 'DANGER'
export type Tone = 'ok' | 'warn' | 'bad'

export interface Wallet { address: string; kind: string }
export interface User { id: number; email: string | null; plan: string; wallets: Wallet[] }
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
  score: number
  verdict: Verdict
  swarm_verdict: string | null
  summary: string
  risks: string[]
  metrics: Metric[]
  buy_recommended: boolean
  price_usd: number | null
  scanned_at: string
}
export interface ScanQueued { address: string; queued: true }

// Phase 2 endpoints (backend not implemented yet)
export interface CopyItem {
  address: string
  ts: string
  side: 'buy' | 'sell'
  token_symbol: string
  amount_usd: number
  reason: string
  risk_tag: 'low' | 'medium' | 'high'
  following: boolean
}
