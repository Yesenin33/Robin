/** Telegram Mini App bridge: when opened from @chainfolio_bot, link (or sign in) with zero typing. */
import { api, session } from '../api/client'

type WebApp = {
  initData: string
  initDataUnsafe: { user?: { allows_write_to_pm?: boolean } }
  ready: () => void
  expand: () => void
  requestWriteAccess?: (cb: (granted: boolean) => void) => void
  openLink?: (url: string, options?: { try_instant_view?: boolean }) => void
  openTelegramLink?: (url: string) => void
}
declare global { interface Window { Telegram?: { WebApp?: WebApp } } }

export const telegramWebApp = (): WebApp | null => {
  const w = window.Telegram?.WebApp
  return w && w.initData ? w : null
}

/** Returns a status line for the UI, or null when not running inside Telegram. */
export async function linkTelegramWebApp(): Promise<{ chat: string; signedIn: boolean } | null> {
  const w = telegramWebApp()
  if (!w) return null
  w.ready()
  w.expand()
  if (w.initDataUnsafe.user && !w.initDataUnsafe.user.allows_write_to_pm && w.requestWriteAccess) {
    await new Promise<boolean>(resolve => w.requestWriteAccess!(resolve)) // so the bot may message this user
  }
  const r = await api.telegramLinkWebApp(w.initData)
  if (r.access_token) session.set(r.access_token)
  return { chat: r.chat, signedIn: !!r.access_token }
}

/**
 * The Mini App webview blocks plain window.open / navigation to other apps. Route every link AppKit (or
 * we) open through Telegram: t.me -> openTelegramLink, anything else -> openLink. Custom wallet schemes
 * (metamask://, wc:) are tried through openLink too and fall back to a direct navigation.
 */
export function installTelegramLinkOpener(): void {
  const tg = window.Telegram?.WebApp
  if (!tg?.initData || !tg.openLink) return
  const w = window as Window & { __cfOpenPatched?: boolean }
  if (w.__cfOpenPatched) return
  w.__cfOpenPatched = true
  const original = window.open.bind(window)
  window.open = ((url?: string | URL, target?: string, features?: string) => {
    const u = url?.toString()
    if (!u) return original(url, target, features)
    try {
      if (/^https?:\/\/t\.me\//i.test(u) && tg.openTelegramLink) tg.openTelegramLink(u)
      else tg.openLink!(u)
    } catch {
      window.location.href = u
    }
    return null
  }) as typeof window.open
}

/** Open a page in the phone's real browser (from the Mini App) or a new tab (everywhere else). */
export function openExternal(url: string): void {
  const tg = telegramWebApp()
  if (tg?.openLink) tg.openLink(url, { try_instant_view: false })
  else window.open(url, '_blank', 'noopener,noreferrer')
}
