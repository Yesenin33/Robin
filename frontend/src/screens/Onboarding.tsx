import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { api, ApiError } from '../api/client'
import { useAuth } from '../lib/auth'
import { isEmail } from '../lib/format'
import { signInWithWallet } from '../lib/siwe'

type Step = 'form' | 'sending' | 'sent'

export function Onboarding() {
  const nav = useNavigate()
  const { user, login, refresh } = useAuth()
  const [email, setEmail] = useState('')
  const [step, setStep] = useState<Step>('form')
  const [error, setError] = useState<string | null>(null)
  const [showEmail, setShowEmail] = useState(false)

  useEffect(() => { if (user) nav('/app/portfolio', { replace: true }) }, [user, nav])

  // The magic link opens in another tab and stores the session -> pick it up here.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => { if (e.key === 'cf_token') void refresh() }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [refresh])

  const sendLink = async () => {
    if (!isEmail(email)) { setError("That e-mail doesn't look right. Check it and try again."); return }
    setError(null)
    setStep('sending')
    try {
      await api.magicLink(email.trim())
      setStep('sent')
    } catch (e) {
      setStep('form')
      setError(e instanceof ApiError && e.status === 429 ? 'Too many tries. Wait a minute and try again.' : "We couldn't send the link. Try again.")
    }
  }

  const connectWallet = async () => {
    setError(null)
    setStep('sending')
    try {
      login(await signInWithWallet())
    } catch (e: any) {
      setStep('form')
      setError(e?.code === 4001 ? 'You cancelled the request in your wallet.' : e?.message ?? "Couldn't connect your wallet.")
    }
  }

  return (
    <div className="page">
      <button onClick={() => nav('/')} style={{ alignSelf: 'flex-start', background: 'none', border: 0, color: 'var(--muted-2)', fontSize: 14, padding: 0 }}>← Back</button>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ fontSize: 30, fontWeight: 800, letterSpacing: '-.02em' }}>Let's get you in</div>
        <div style={{ color: 'var(--muted)', fontSize: 15, lineHeight: 1.5 }}>We set up your account for you. It takes about 10 seconds.</div>
      </div>

      {error && <div className="notice-error" role="alert">{error}</div>}

      {step === 'sending' && (
        <div className="card" style={{ padding: '28px 20px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14, textAlign: 'center' }}>
          <div className="spinner" />
          <div style={{ fontWeight: 700, fontSize: 17 }}>Setting up your account…</div>
          <div style={{ color: 'var(--muted-2)', fontSize: 14 }}>Creating your secure wallet. Nothing to write down.</div>
        </div>
      )}

      {step === 'sent' && (
        <div className="card" style={{ padding: '28px 20px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, textAlign: 'center' }}>
          <div style={{ fontSize: 32 }}>✉️</div>
          <div style={{ fontWeight: 800, fontSize: 20 }}>Check your inbox</div>
          <div style={{ color: 'var(--muted)', fontSize: 14, lineHeight: 1.5 }}>
            We sent a sign-in link to <b style={{ color: '#fff' }}>{email}</b>. Tap it and you're in.
          </div>
          <button className="btn btn-primary" style={{ marginTop: 6, height: 48, padding: '0 24px', borderRadius: 14, fontSize: 15 }} onClick={() => void refresh()}>
            I tapped the link
          </button>
          <button onClick={() => setStep('form')} style={{ background: 'none', border: 0, color: 'var(--muted-2)', fontSize: 13 }}>Use a different e-mail</button>
        </div>
      )}

      {step === 'form' && (
        <>
          <button className="btn btn-primary" style={{ height: 54 }} onClick={() => void connectWallet()}>
            Connect wallet
          </button>
          <div style={{ color: 'var(--muted-2)', fontSize: 13, textAlign: 'center', lineHeight: 1.45 }}>
            Any Robinhood Chain wallet. Nothing to write down, nothing we can touch.
          </div>
          {!showEmail ? (
            <button onClick={() => setShowEmail(true)} style={{ background: 'none', border: 0, color: 'var(--muted-2)', fontSize: 13 }}>
              Use e-mail instead
            </button>
          ) : (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, color: 'var(--faint)', fontSize: 13 }}>
                <div style={{ flex: 1, height: 1, background: 'var(--line)' }} />or<div style={{ flex: 1, height: 1, background: 'var(--line)' }} />
              </div>
              <form style={{ display: 'flex', flexDirection: 'column', gap: 12 }} onSubmit={e => { e.preventDefault(); void sendLink() }}>
                <label className="label" htmlFor="email">E-mail</label>
                <input id="email" className="input" type="email" inputMode="email" autoComplete="email"
                  placeholder="alex@email.com" value={email} onChange={e => setEmail(e.target.value)} />
                <button className="btn btn-ghost" type="submit">Send me a sign-in link</button>
              </form>
            </>
          )}
        </>
      )}

      <div style={{ marginTop: 'auto', display: 'flex', gap: 10, alignItems: 'flex-start', color: 'var(--muted-2)', fontSize: 13, lineHeight: 1.5 }}>
        <span>🔒</span><span>Only you can move your money. We can never touch it.</span>
      </div>
    </div>
  )
}
