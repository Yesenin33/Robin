import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { api } from '../api/client'
import { Toggle, toast } from '../components/ui'
import { useAuth } from '../lib/auth'
import { shortAddr } from '../lib/format'
import { disablePush, enablePush } from '../lib/push'
import { useAsync } from '../lib/useAsync'


export function Profile() {
  const { user, logout } = useAuth()
  const nav = useNavigate()
  const [upgrading, setUpgrading] = useState(false)
  const { state: ch, reload: reloadChannels } = useAsync(() => api.alertChannels(), [])
  const { state: alerts, reload: reloadAlerts } = useAsync(() => api.alerts(), [])
  const [tgStep, setTgStep] = useState<'idle' | 'chat' | 'code'>('idle')
  const [tgInput, setTgInput] = useState('')
  const channels = ch.status === 'ready' ? ch.data : { push: false, telegram: false, telegram_bot: null }

  const flipPush = async () => {
    try {
      if (channels.push) await disablePush()
      else await enablePush()
      await reloadChannels(true)
    } catch (e: any) {
      toast(e?.status === 503 ? 'Phone alerts are not set up yet' : e?.message ?? "Couldn't change phone alerts")
    }
  }

  const flipTelegram = async () => {
    if (channels.telegram) { await api.telegramUnlink(); await reloadChannels(true); return }
    setTgStep(tgStep === 'idle' ? 'chat' : 'idle')
  }

  const submitTelegram = async () => {
    try {
      if (tgStep === 'chat') {
        await api.telegramStart(tgInput.trim())
        setTgStep('code'); setTgInput(''); toast('We sent a code to your Telegram')
      } else {
        await api.telegramVerify(tgInput.trim())
        setTgStep('idle'); setTgInput(''); await reloadChannels(true); toast('Telegram alerts are on')
      }
    } catch (e: any) {
      toast(e?.message ?? "Couldn't connect Telegram")
    }
  }

  const openAlert = async (id: number, url: string | null) => {
    await api.alertRead(id).catch(() => undefined)
    if (url) nav(url)
    else void reloadAlerts(true)
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
    { label: 'Phone alerts', sub: 'Web Push · risky moves in your holdings', on: channels.push, flip: flipPush },
    { label: 'Telegram alerts', sub: 'Get alerts in your Telegram chat', on: channels.telegram, flip: flipTelegram },
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

      {tgStep !== 'idle' && (
        <form className="card" style={{ borderRadius: 22, padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}
          onSubmit={e => { e.preventDefault(); void submitTelegram() }}>
          <div style={{ fontSize: 14, color: 'var(--muted)', lineHeight: 1.45 }}>
            {tgStep === 'chat'
              ? <>Open {channels.telegram_bot ? <a href={`https://t.me/${channels.telegram_bot}`} target="_blank" rel="noreferrer">@{channels.telegram_bot}</a> : 'our bot'} in Telegram, press Start, then enter your chat ID.</>
              : 'Enter the 6-digit code we sent to your Telegram.'}
          </div>
          <input className="input" style={{ height: 48 }} inputMode="numeric" value={tgInput} onChange={e => setTgInput(e.target.value)}
            placeholder={tgStep === 'chat' ? 'Chat ID, e.g. 123456789' : '6-digit code'} />
          <button className="btn btn-primary" style={{ height: 46, fontSize: 15, borderRadius: 14 }} type="submit">
            {tgStep === 'chat' ? 'Send me a code' : 'Turn on Telegram alerts'}
          </button>
        </form>
      )}

      {alerts.status === 'ready' && alerts.data.items.length > 0 && (
        <>
          <div className="label">Recent alerts{alerts.data.unread ? ` · ${alerts.data.unread} new` : ''}</div>
          <div className="card" style={{ borderRadius: 22, overflow: 'hidden' }}>
            {alerts.data.items.slice(0, 5).map(a => (
              <div key={a.id} className="list-row" style={{ cursor: 'pointer' }} onClick={() => void openAlert(a.id, a.url)}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: a.read ? 600 : 800, fontSize: 15 }}>{a.title}</div>
                  <div style={{ fontSize: 13, color: 'var(--muted-2)', marginTop: 2 }}>{a.body}</div>
                </div>
                {!a.read && <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--green)', flex: 'none' }} />}
              </div>
            ))}
          </div>
        </>
      )}

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
