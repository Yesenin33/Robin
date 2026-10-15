import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { api } from '../api/client'
import { Toggle, toast } from '../components/ui'
import { useAuth } from '../lib/auth'
import { shortAddr } from '../lib/format'

const PREFS_KEY = 'cf_alerts'
type Prefs = { push: boolean; tg: boolean }
const loadPrefs = (): Prefs => { try { return { push: false, tg: false, ...JSON.parse(localStorage.getItem(PREFS_KEY) ?? '{}') } } catch { return { push: false, tg: false } } }

export function Profile() {
  const { user, logout } = useAuth()
  const nav = useNavigate()
  const [prefs, setPrefs] = useState<Prefs>(loadPrefs)
  const [upgrading, setUpgrading] = useState(false)

  const save = (p: Prefs) => { setPrefs(p); try { localStorage.setItem(PREFS_KEY, JSON.stringify(p)) } catch { /* ignore */ } }

  const flipPush = async () => {
    if (!prefs.push) {
      if (!('Notification' in window)) { toast("This browser can't show alerts"); return }
      if ((await Notification.requestPermission()) !== 'granted') { toast('Allow notifications to get alerts'); return }
    }
    save({ ...prefs, push: !prefs.push })
  }

  const upgrade = async () => {
    setUpgrading(true)
    try {
      window.location.href = (await api.checkout()).url
    } catch {
      toast('Upgrades are coming soon')
      setUpgrading(false)
    }
  }

  const plus = user?.plan === 'plus'
  const account = user?.email ?? (user?.wallets[0] ? shortAddr(user.wallets[0].address) : '')
  const toggles = [
    { label: 'Phone alerts', sub: 'Web Push · risky moves in your holdings', on: prefs.push, flip: flipPush },
    { label: 'Telegram alerts', sub: 'Get alerts in your Telegram chat', on: prefs.tg, flip: () => save({ ...prefs, tg: !prefs.tg }) },
  ]
  const settings = [
    { k: 'Account', v: account },
    { k: 'Currency', v: 'USD' },
    { k: 'Help & support', v: '', href: 'mailto:support@chainfolio.app' },
    { k: 'Legal', v: '' },
  ]

  return (
    <div className="app-page">
      <div className="h1">Profile</div>
      {plus ? (
        <div style={{ background: 'var(--green-soft)', border: '1px solid #14501F', borderRadius: 22, padding: 18, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ fontWeight: 800, fontSize: 17 }}>Chainfolio Plus</div>
            <div style={{ background: 'var(--green)', color: 'var(--green-ink)', fontSize: 12, fontWeight: 800, padding: '4px 10px', borderRadius: 99 }}>ACTIVE</div>
          </div>
          <div style={{ color: 'var(--muted)', fontSize: 14 }}>$4.99 / month</div>
        </div>
      ) : (
        <div className="card" style={{ borderRadius: 22, padding: 18, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ fontWeight: 800, fontSize: 17 }}>Free plan</div>
            <div className="pill">FREE</div>
          </div>
          <div style={{ color: 'var(--muted)', fontSize: 14 }}>Plus: unlimited safety checks and instant alerts — $4.99 / month.</div>
          <button className="btn btn-primary" style={{ height: 46, fontSize: 15, borderRadius: 14 }} disabled={upgrading} onClick={() => void upgrade()}>Get Plus</button>
        </div>
      )}

      <div className="label">Alerts</div>
      <div className="card" style={{ borderRadius: 22, overflow: 'hidden' }}>
        {toggles.map(t => (
          <div key={t.label} className="list-row" style={{ cursor: 'pointer' }} onClick={() => void t.flip()}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 700, fontSize: 15 }}>{t.label}</div>
              <div style={{ fontSize: 13, color: 'var(--muted-2)', marginTop: 2 }}>{t.sub}</div>
            </div>
            <Toggle on={t.on} />
          </div>
        ))}
      </div>

      <div className="label">Settings</div>
      <div className="card" style={{ borderRadius: 22, overflow: 'hidden' }}>
        {settings.map(s => {
          const row = (
            <div className="list-row" style={{ justifyContent: 'space-between', fontSize: 15, fontWeight: 600 }}>
              <span>{s.k}</span><span style={{ color: 'var(--muted-2)', fontWeight: 500 }}>{s.v} ›</span>
            </div>
          )
          return s.href ? <a key={s.k} href={s.href} style={{ color: 'inherit', textDecoration: 'none' }}>{row}</a> : <div key={s.k}>{row}</div>
        })}
      </div>

      <button className="btn btn-danger" onClick={() => { logout(); nav('/') }}>Sign out</button>
    </div>
  )
}
