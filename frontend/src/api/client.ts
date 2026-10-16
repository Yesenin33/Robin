import type { Alerts, Channels, CopyItem, Portfolio, Safety, ScanQueued, Suggested, TokenOut, User } from './types'

const BASE = import.meta.env.VITE_API_URL ?? '/api'
const TOKEN_KEY = 'cf_token'

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

export const session = {
  get: (): string | null => { try { return localStorage.getItem(TOKEN_KEY) } catch { return null } },
  set: (t: string) => { try { localStorage.setItem(TOKEN_KEY, t) } catch { /* private mode */ } },
  clear: () => { try { localStorage.removeItem(TOKEN_KEY) } catch { /* private mode */ } },
}

async function req<T>(path: string, init: RequestInit = {}): Promise<{ status: number; data: T }> {
  const headers = new Headers(init.headers)
  if (init.body) headers.set('Content-Type', 'application/json')
  const token = session.get()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  let res: Response
  try {
    res = await fetch(BASE + path, { ...init, headers })
  } catch {
    throw new ApiError(0, 'Network error')
  }
  const body = await res.json().catch(() => null)
  if (!res.ok) {
    const detail = body?.detail
    throw new ApiError(res.status, typeof detail === 'string' ? detail : res.statusText)
  }
  return { status: res.status, data: body as T }
}

const get = async <T>(p: string) => (await req<T>(p)).data
const post = async <T>(p: string, body?: unknown) =>
  (await req<T>(p, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })).data

export const api = {
  siweNonce: () => get<{ nonce: string }>('/auth/siwe/nonce'),
  siwe: (message: string, signature: string, link_token?: string) =>
    post<TokenOut>('/auth/siwe', { message, signature, link_token }),
  linkToken: () => post<{ token: string; expires_in: number }>('/auth/link-token'),
  me: () => get<User>('/auth/me'),

  portfolio: (address: string) => get<Portfolio>(`/portfolio/${address}`),

  /** 200 -> result, 202 -> scan running (caller polls) */
  safety: async (ca: string): Promise<Safety | ScanQueued> => (await req<Safety | ScanQueued>(`/safety/${ca}`)).data,

  copyFeed: () => get<CopyItem[]>('/copy/feed'),
  copySuggested: () => get<Suggested[]>('/copy/suggested'),
  copySubscribe: (address: string, follow: boolean) => post('/copy/subscribe', { address, follow }),

  alerts: () => get<Alerts>('/alerts'),
  alertRead: (id: number) => post(`/alerts/${id}/read`),
  alertChannels: () => get<Channels>('/alerts/channels'),
  pushKey: () => get<{ public_key: string }>('/alerts/push/key'),
  pushSubscribe: (sub: PushSubscriptionJSON) => post('/alerts/push/subscribe', sub),
  pushUnsubscribe: () => req('/alerts/push/subscribe', { method: 'DELETE' }),
  telegramLink: () => post<{ url: string; code: string; expires_in: number }>('/alerts/telegram/link'),
  telegramLinkWebApp: (init_data: string) =>
    post<{ linked: boolean; chat: string; can_message: boolean; access_token: string | null }>('/alerts/telegram/link-webapp', { init_data }),
  telegramVerify: (code: string) => post<Channels>('/alerts/telegram/verify', { code }),
  telegramUnlink: () => req('/alerts/telegram', { method: 'DELETE' }),
  checkout: () => post<{ url: string }>('/billing/checkout'),
}

export const isQueued = (r: Safety | ScanQueued): r is ScanQueued => 'queued' in r
