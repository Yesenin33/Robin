import { useEffect, useState } from 'react'
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
  const { state: ch, reload: reloadChannels } = useAsync(() => api.alertChannels(), [])
  const { state: alerts, reload: reloadAlerts } = useAsync(() => api.alerts(), [])
  const [tgLink, setTgLink] = useState<{ url: string; code: string } | null>(null)
  const [tgInput, setTgInput] = useState('')
  const channels = ch.status === 'ready' ? ch.data : { push: false, telegram: false, telegram_bot: null }

  // while the "Open @bot" card is up, watch for the bot confirming the link
  useEffect(() => {
    if (!tgLink) return
    const t = setInterval(() => { void reloadChannels(true) }, 3000)
    return () => clearInterval(t)
  }, [tgLink, reloadChannels])
  useEffect(() => {
    if (tgLink && channels.telegram) { setTgLink(null); toast('Telegram alerts: connected') }
  }, [tgLink, channels.telegram])

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
    if (tgLink) { setTgLink(null); return }
    try {
      setTgLink(await api.telegramLink())
    } catch (e: any) {
      toast(e?.status === 503 ? 'Telegram alerts are not set up yet' : "Couldn't start Telegram linking")
    }
  }

  const submitBotCode = async () => {
    try {
      await api.telegramVerify(tgInput.trim())
      setTgInput(''); setTgLink(null); await reloadChannels(true); toast('Telegram alerts: connected')
    } catch {
      toast('That code is wrong or expired')
    }
  }

  const openAlert = async (id: number, url: string | null) => {
    await api.alertRead(id).catch(() => undefined)
    if (url) nav(url)
    else void reloadAlerts(true)
  }

  const wallet = user?.wallets[0]?.address
  const account = wallet ? shortAddr(wallet) : 'No wallet'
  const toggles = [
    { label: 'Phone alerts', sub: 'Web Push · risky moves in your holdings', on: channels.push, flip: flipPush },
    { label: 'Telegram alerts', sub: channels.telegram ? 'Connected — alerts arrive in Telegram' : 'Not connected — tap to connect',
      on: channels.telegram, flip: flipTelegram },
  ]
  const settings = [
    { k: 'Account', v: account },
    { k: 'Currency', v: 'USD' },
    { k: 'Help & support', v: '', href: 'mailto:support@chainfolio.app' },
    { k: 'Legal', v: '' },
  ]

  return (
    <div className="app-page">
      <h1 className="display">Profile</h1>
      {!wallet && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, alignItems: 'flex-start' }}>
          <div className="notice" style={{ fontSize: 16, padding: '18px 20px', borderRadius: 18 }}>No wallet connected. Connect one to see your portfolio and get alerts.</div>
          <button className="btn btn-primary" style={{ height: 52 }} onClick={() => nav('/app')}>Connect wallet</button>
        </div>
      )}
      <div className="grid-2" style={{ gap: 24 }}>
        <div className="stack" style={{ gap: 12 }}>
          <div className="dark-card" style={{ padding: 22, display: 'flex', alignItems: 'center', gap: 14 }}>
            <div className="avatar" style={{ width: 48, height: 48, background: 'var(--lime)' }}>0x</div>
            <div style={{ flex: 1 }}>
              <div className="num" style={{ fontWeight: 700, fontSize: 17 }}>{account}</div>
              <div style={{ fontSize: 14, color: '#B9B3A6', marginTop: 2 }}>Robinhood Chain · {wallet ? 'connected' : 'not connected'}</div>
            </div>
          </div>
          <div className="label" style={{ marginTop: 8 }}>Alerts</div>
          <div className="group">
            {toggles.map(t => (
              <div key={t.label} className="list-row" role="button" tabIndex={0} style={{ cursor: 'pointer', minHeight: 72 }}
                onClick={() => void t.flip()} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); void t.flip() } }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 700, fontSize: 16 }}>{t.label}</div>
                  <div style={{ fontSize: 14, color: 'var(--muted)', marginTop: 2, lineHeight: 1.4 }}>{t.sub}</div>
                </div>
                <Toggle on={t.on} />
              </div>
            ))}
          </div>

          {tgLink && (
            <div className="card" style={{ borderRadius: 22, padding: 18, display: 'flex', flexDirection: 'column', gap: 12 }}>
              <a className="btn btn-primary" href={tgLink.url} target="_blank" rel="noreferrer" style={{ height: 52, fontSize: 16 }}>
                Open @{channels.telegram_bot ?? 'chainfolio_bot'}
              </a>
              <div style={{ fontSize: 14, lineHeight: 1.5, color: 'var(--body)' }}>
                Tap the button and press Start. You're connected. On another device? Send the bot this code:{' '}
                <b className="num" style={{ color: 'var(--ink)', letterSpacing: '.12em' }}>{tgLink.code}</b>
              </div>
              <form style={{ display: 'flex', gap: 8 }} onSubmit={e => { e.preventDefault(); void submitBotCode() }}>
                <input className="input" style={{ height: 48, fontSize: 15, padding: '0 18px', background: 'var(--paper)' }} inputMode="numeric" maxLength={6} value={tgInput}
                  onChange={e => setTgInput(e.target.value)} placeholder="Code from the bot" aria-label="Code from the bot" />
                <button className="btn btn-ghost btn-sm" type="submit">Connect</button>
              </form>
            </div>
          )}
        </div>

        <div className="stack" style={{ gap: 12 }}>
          {alerts.status === 'ready' && alerts.data.items.length > 0 && (
            <>
              <div className="label">Recent alerts{alerts.data.unread ? ` · ${alerts.data.unread} new` : ''}</div>
              <div className="group">
                {alerts.data.items.slice(0, 5).map(a => (
                  <div key={a.id} className="list-row" role="button" tabIndex={0} style={{ cursor: 'pointer' }} onClick={() => void openAlert(a.id, a.url)}
                    onKeyDown={e => { if (e.key === 'Enter') void openAlert(a.id, a.url) }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: a.read ? 600 : 800, fontSize: 15 }}>{a.title}</div>
                      <div style={{ fontSize: 14, color: 'var(--muted)', marginTop: 2 }}>{a.body}</div>
                    </div>
                    {!a.read && <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--green)', flex: 'none' }} />}
                  </div>
                ))}
              </div>
            </>
          )}
          <div className="label" style={{ marginTop: 8 }}>Settings</div>
          <div className="group">
            {settings.map(s => {
              const row = (
                <div className="list-row" style={{ justifyContent: 'space-between', height: 56, padding: '0 18px', fontSize: 16, fontWeight: 600 }}>
                  <span>{s.k}</span><span style={{ color: 'var(--muted)', fontWeight: 500 }}>{s.v} ›</span>
                </div>
              )
              return s.href ? <a key={s.k} href={s.href} style={{ color: 'inherit', textDecoration: 'none' }}>{row}</a> : <div key={s.k}>{row}</div>
            })}
          </div>
          <button className="btn btn-danger" style={{ marginTop: 8 }} onClick={() => { logout(); nav('/') }}>Sign out</button>
        </div>
      </div>
    </div>
  )
}
