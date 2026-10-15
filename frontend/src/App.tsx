import { useEffect, useRef } from 'react'
import { Navigate, Outlet, Route, Routes, useLocation, useNavigate } from 'react-router-dom'

import { Background } from './components/Background'
import { TabBar } from './components/TabBar'
import { toast, Toaster } from './components/ui'
import { useAuth } from './lib/auth'
import { linkTelegramWebApp } from './lib/telegram'
import { AuthCallback } from './screens/AuthCallback'
import { ConnectGate } from './screens/ConnectGate'
import { Copy } from './screens/Copy'
import { Landing } from './screens/Landing'
import { Onboarding } from './screens/Onboarding'
import { Portfolio } from './screens/Portfolio'
import { Profile } from './screens/Profile'
import { Safety } from './screens/Safety'

// Phone surface opacity per area, as in the design (background shows through more on landing)
const alphaFor = (path: string) => (path === '/' ? 0.62 : path.startsWith('/app') ? 0.86 : 0.72)

function AppLayout() {
  const { user, ready } = useAuth()
  if (!ready) return <div className="center-state"><div className="spinner" /></div>
  if (!user) return <Navigate to="/login" replace />
  // wallet = identity: a session without a wallet gets one blocking step instead of the app
  if (user.wallets.length === 0) return <div className="scroll"><ConnectGate /></div>
  return (
    <>
      <div className="scroll"><Outlet /></div>
      <TabBar />
    </>
  )
}

/** Opened from the bot's "Open Chainfolio" button: link Telegram (and sign in if needed), no typing. */
function TelegramBridge() {
  const { ready, refresh } = useAuth()
  const nav = useNavigate()
  const done = useRef(false)
  useEffect(() => {
    if (!ready || done.current) return
    done.current = true
    linkTelegramWebApp()
      .then(async r => {
        if (!r) return
        await refresh()
        toast('Telegram alerts: connected')
        nav('/app/profile', { replace: true })
      })
      .catch(() => toast("Couldn't connect Telegram — try again from Profile"))
  }, [ready, refresh, nav])
  return null
}

export function App() {
  const { pathname } = useLocation()
  return (
    <>
      <Background />
      <div className="shell">
        <main className="phone" style={{ background: `rgba(11,14,17,${alphaFor(pathname)})` }}>
          <Routes>
            <Route path="/" element={<div className="scroll"><Landing /></div>} />
            <Route path="/login" element={<div className="scroll"><Onboarding /></div>} />
            <Route path="/auth/callback" element={<div className="scroll"><AuthCallback /></div>} />
            <Route path="/app" element={<AppLayout />}>
              <Route index element={<Navigate to="portfolio" replace />} />
              <Route path="portfolio" element={<Portfolio />} />
              <Route path="safety/:ca?" element={<Safety />} />
              <Route path="copy" element={<Copy />} />
              <Route path="profile" element={<Profile />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
      <Toaster />
      <TelegramBridge />
    </>
  )
}
