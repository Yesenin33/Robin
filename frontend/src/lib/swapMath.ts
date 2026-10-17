/** Pure helpers for the swap sheet (unit-tested with `npm run test`). Amounts are bigint base units. */
export function parseUnits(text: string, decimals: number): bigint | null {
  const t = text.trim().replace(',', '.')
  if (!/^\d*\.?\d*$/.test(t) || t === '' || t === '.') return null
  const [whole, frac = ''] = t.split('.')
  if (frac.length > decimals) return null
  const v = BigInt(whole || '0') * 10n ** BigInt(decimals) + BigInt(frac.padEnd(decimals, '0') || '0')
  return v > 0n ? v : null
}

export function formatUnits(raw: bigint | string, decimals: number, maxFrac = 6): string {
  const v = typeof raw === 'string' ? BigInt(raw) : raw
  const base = 10n ** BigInt(decimals)
  const whole = v / base
  let frac = (v % base).toString().padStart(decimals, '0').slice(0, maxFrac).replace(/0+$/, '')
  if (v > 0n && whole === 0n && frac === '') frac = '0'.padEnd(maxFrac, '0').slice(0, maxFrac - 1) + '1'  // never show 0 for a real amount
  return frac ? `${whole.toLocaleString('en-US')}.${frac}` : whole.toLocaleString('en-US')
}

/** wallet rejection: EIP-1193 4001 (and ethers' ACTION_REJECTED). Quiet - the user said no. */
export const isRejection = (e: unknown): boolean => {
  const x = e as { code?: number | string; cause?: { code?: number } } | null
  return x?.code === 4001 || x?.code === 'ACTION_REJECTED' || x?.cause?.code === 4001
}

export const pctText = (bps: number) => `${(bps / 100).toFixed(bps % 100 ? 2 : 1).replace(/\.?0+$/, '')}%`
