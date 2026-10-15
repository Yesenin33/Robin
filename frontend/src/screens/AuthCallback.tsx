import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { api } from '../api/client'
import { EmptyState } from '../components/States'
import { useAuth } from '../lib/auth'

/** Target of the e-mail magic link: /auth/callback?token=... */
export function AuthCallback() {
  const [params] = useSearchParams()
  const nav = useNavigate()
  const { login } = useAuth()
  const [failed, setFailed] = useState(false)
  const once = useRef(false) // link is single-use; guard StrictMode double effect

  useEffect(() => {
    if (once.current) return
    once.current = true
    const token = params.get('token')
    if (!token) { setFailed(true); return }
    api.verify(token)
      .then(t => { login(t); nav('/app/portfolio', { replace: true }) })
      .catch(() => setFailed(true))
  }, [params, login, nav])

  return (
    <div className="page">
      {failed
        ? <EmptyState emoji="⌛" title="This link has expired" body="Sign-in links work once and only for 15 minutes. Ask for a new one." cta="Get a new link" onCta={() => nav('/login')} />
        : <div className="center-state"><div className="spinner" /><div style={{ fontWeight: 700, fontSize: 17 }}>Signing you in…</div></div>}
    </div>
  )
}
