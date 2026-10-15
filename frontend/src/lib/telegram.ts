/** Telegram Mini App bridge: when opened from @chainfolio_bot, link (or sign in) with zero typing. */
import { api, session } from '../api/client'

type WebApp = {
  initData: string
  initDataUnsafe: { user?: { allows_write_to_pm?: boolean } }
  ready: () => void
  expand: () => void
  requestWriteAccess?: (cb: (granted: boolean) => void) => void
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
