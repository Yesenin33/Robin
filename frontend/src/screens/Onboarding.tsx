import { useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { Logo } from '../components/Logo'
import { WalletConnectPanel } from '../components/WalletConnect'
import { useAuth } from '../lib/auth'

const LockIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#110E08" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" style={{ flex: 'none', marginTop: 2 }}><path d="M7 11V8a5 5 0 0 1 10 0v3 M5 11h14v10H5z" /></svg>
)

/** Sign-in is wallet-only: there is no e-mail path. The wallet is the account. */
export function Onboarding() {
  const nav = useNavigate()
  const { user, login, refresh } = useAuth()

  useEffect(() => { if (user) nav('/app/portfolio', { replace: true }) }, [user, nav])

  // A wallet connected elsewhere (Telegram "open in browser" -> link token) lands in storage: pick it up.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => { if (e.key === 'cf_token') void refresh() }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [refresh])

  return (
    <div className="split">
      <div className="split-side">
        <Link to="/" style={{ textDecoration: 'none' }}><Logo size={32} onDark /></Link>
        <div className="serif" style={{ fontSize: 68, lineHeight: 1.02, letterSpacing: '-.03em', maxWidth: 480 }}>
          Your wallet is <i style={{ color: 'var(--lime)' }}>your account.</i>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, fontSize: 16, lineHeight: 1.5, color: '#D3CEC2', maxWidth: 420 }}>
          <div>Nothing to write down. No password to lose.</div>
          <div>We read balances so we can show your portfolio. We can never move your money.</div>
        </div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', padding: '16px 20px 28px', minHeight: '100dvh' }} className="login-col">
        <button onClick={() => nav('/')} style={{ alignSelf: 'flex-start', height: 44, background: 'none', border: 0, color: 'var(--muted)', fontSize: 15, fontWeight: 600, padding: 0 }}>← Back</button>
        <div style={{ margin: 'auto', width: '100%', maxWidth: 420, display: 'flex', flexDirection: 'column', gap: 24, padding: '24px 0' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div className="serif" style={{ fontSize: 44, lineHeight: 1.02, letterSpacing: '-.03em' }}>Let's get you in</div>
            <div style={{ color: 'var(--body)', fontSize: 16, lineHeight: 1.5 }}>Connect your wallet. It's your account, and it takes about 10 seconds.</div>
          </div>
          <WalletConnectPanel onConnected={login} />
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', color: 'var(--muted)', fontSize: 14, lineHeight: 1.5 }}>
          <LockIcon /><span>Only you can move your money. We can never touch it.</span>
        </div>
      </div>
    </div>
  )
}
