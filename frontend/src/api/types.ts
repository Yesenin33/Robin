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
  image_url?: string | null
  amount: number
  price_usd: number | null
  value_usd: number
  change_24h_pct: number | null
  pnl_pct: number | null
  verdict: Verdict | null
  liquidity_usd?: number | null  // depth of its pool; null = no market data
  counted?: boolean  // false = listed but not in total_usd
  not_counted_reason?: string | null
}
export interface Portfolio {
  address: string
  total_usd: number
  change_24h_usd: number
  change_24h_pct: number
  holdings: Holding[]
  excluded_count?: number  // holdings worth more on paper than their pool can pay out: not listed, not counted
  excluded?: Holding[]  // the rows behind excluded_count, shown so the owner can see what was left out
  not_counted_count?: number  // listed, but our own checks can't vouch they can be sold: not in total_usd
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
  refreshing?: boolean  // this is the previous result; a fresh check is running in the background
  image_url?: string | null
}
export interface ScanQueued {
  address: string; queued: true; state?: 'queued' | 'running'; retry_after_s?: number
  stage?: string | null  // contract | market | transfers | holders | scoring
  eta_s?: number | null  // measured median of recent scans
}

export type RiskTag = 'low' | 'medium' | 'high'

export interface CopyItem {
  address: string
  ts: string
  side: 'buy' | 'sell'
  token_symbol: string
  token_address: string | null
  token_image_url?: string | null
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

export interface SwapConfig { enabled: boolean; chain_id: number; default_slippage_bps: number; max_slippage_bps: number }
export interface SwapTx { to: string; data: string; value: string }
export interface SwapStep { kind: 'approve_token' | 'approve_router' | 'swap'; label: string; tx: SwapTx }
export interface SwapQuote {
  source: string
  route: 'v3' | 'v4'
  side: 'buy' | 'sell'
  token: string
  pay_with: 'ETH' | 'USDG'
  amount_in_raw: string
  amount_out_raw: string
  min_out_raw: string
  decimals_in: number
  decimals_out: number
  slippage_bps: number
  fee_bps: number
  price_impact_pct: number | null  // null = we have no price to compare against
  deadline: number
  steps: SwapStep[]
  chain_id: number
}
