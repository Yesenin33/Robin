export const usd = (v: number) =>
  '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export const usdCompact = (v: number) =>
  v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `$${(v / 1e3).toFixed(0)}K` : `$${v.toFixed(0)}`

export const signedPct = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`

export const amount = (v: number) => {
  if (!Number.isFinite(v)) return '—'
  if (v === 0) return '0'
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`
  if (v >= 1e3) return v.toLocaleString('en-US', { maximumFractionDigits: 0 })
  // A balance the wallet really has must never render as a bare "0": 18-decimal dust rounds to zero at
  // this precision, and "0 musebook" reads as a broken row rather than as a holding.
  if (v < 1e-6) return '<0.000001'
  return v.toLocaleString('en-US', { maximumFractionDigits: v < 1 ? 6 : 2 })
}

export const shortAddr = (a: string) => `${a.slice(0, 6)}…${a.slice(-4)}`

export const ago = (iso: string) => {
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (m < 60) return `${Math.max(m, 1)} min ago`
  const h = Math.round(m / 60)
  return h < 24 ? `${h} hr ago` : `${Math.round(h / 24)} d ago`
}

export const isAddress = (v: string) => /^0x[a-fA-F0-9]{40}$/.test(v.trim())

const PALETTE = ['#E4DCC3', '#CFE3B4', '#F0D9A8', '#D8D2E8', '#C9DDE0', '#EBCFC4']
export const colorFor = (seed: string) => {
  let h = 0
  for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return PALETTE[h % PALETTE.length]
}

