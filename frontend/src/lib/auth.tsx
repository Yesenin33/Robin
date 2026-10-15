import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'

import { api, ApiError, session } from '../api/client'
import type { TokenOut, User } from '../api/types'

interface AuthCtx {
  user: User | null
  ready: boolean
  login: (t: TokenOut) => void
  logout: () => void
  refresh: () => Promise<void>
}

const Ctx = createContext<AuthCtx | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [ready, setReady] = useState(false)

  const refresh = useCallback(async () => {
    if (!session.get()) { setUser(null); setReady(true); return }
    try {
      setUser(await api.me())
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) session.clear()
      setUser(null)
    } finally {
      setReady(true)
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  const login = (t: TokenOut) => { session.set(t.access_token); setUser(t.user) }
  const logout = () => { session.clear(); setUser(null) }

  return <Ctx.Provider value={{ user, ready, login, logout, refresh }}>{children}</Ctx.Provider>
}

export function useAuth(): AuthCtx {
  const c = useContext(Ctx)
  if (!c) throw new Error('useAuth outside AuthProvider')
  return c
}
