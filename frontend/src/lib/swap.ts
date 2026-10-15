/**
 * Buy/Sell deep link into an external swap UI (non-custodial: the user signs in their own wallet).
 * VITE_SWAP_URL is a template; placeholders:
 *   {ca}   token address          {side} buy | sell
 *   {in}   input currency  (buy: ETH, sell: token)
 *   {out}  output currency (buy: token, sell: ETH)
 * e.g. https://app.uniswap.org/swap?chain=<slug>&inputCurrency={in}&outputCurrency={out}
 */
const TEMPLATE = (import.meta.env.VITE_SWAP_URL as string | undefined)?.trim() ?? ''
const NATIVE = (import.meta.env.VITE_SWAP_NATIVE as string | undefined)?.trim() || 'ETH'

export const swapEnabled = TEMPLATE.length > 0

export function swapUrl(ca: string, side: 'buy' | 'sell'): string | null {
  if (!swapEnabled) return null
  const [input, output] = side === 'buy' ? [NATIVE, ca] : [ca, NATIVE]
  const fill: Record<string, string> = { ca, side, in: encodeURIComponent(input), out: encodeURIComponent(output) }
  return Object.entries(fill).reduce((url, [k, v]) => url.split(`{${k}}`).join(v), TEMPLATE)
}
