/** One-time token from the Telegram Mini App ("open in browser"): lets this browser attach its wallet to that account. */
const KEY = 'cf_link'

export function readLinkToken(): string | null {
  try {
    const fromUrl = new URLSearchParams(window.location.search).get('link')
    if (fromUrl) sessionStorage.setItem(KEY, fromUrl)
    return sessionStorage.getItem(KEY)
  } catch {
    return null
  }
}

export function clearLinkToken(): void {
  try { sessionStorage.removeItem(KEY) } catch { /* ignore */ }
}
