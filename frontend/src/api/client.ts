import type { CopyItem, Portfolio, Safety, ScanQueued, TokenOut, User } from './types'

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
  magicLink: (email: string) => post<{ sent: boolean }>('/auth/magic-link', { email }),
  verify: (token: string) => get<TokenOut>(`/auth/verify?token=${encodeURIComponent(token)}`),
  siweNonce: () => get<{ nonce: string }>('/auth/siwe/nonce'),
  siwe: (message: string, signature: string) => post<TokenOut>('/auth/siwe', { message, signature }),
  me: () => get<User>('/auth/me'),

  portfolio: (address: string) => get<Portfolio>(`/portfolio/${address}`),

  /** 200 -> result, 202 -> scan running (caller polls) */
  safety: async (ca: string): Promise<Safety | ScanQueued> => (await req<Safety | ScanQueued>(`/safety/${ca}`)).data,

  // Phase 2
  copyFeed: () => get<CopyItem[]>('/copy/feed'),
  copySubscribe: (address: string, follow: boolean) => post('/copy/subscribe', { address, follow }),
  checkout: () => post<{ url: string }>('/billing/checkout'),
}

export const isQueued = (r: Safety | ScanQueued): r is ScanQueued => 'queued' in r
