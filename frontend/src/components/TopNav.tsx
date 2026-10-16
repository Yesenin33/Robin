import { Link, NavLink } from 'react-router-dom'

import { useAuth } from '../lib/auth'
import { shortAddr } from '../lib/format'
import { Logo } from './Logo'
import { TABS } from './TabBar'

/** Tablet / desktop navigation (the bottom tab bar takes over below 768 px). */
export function TopNav() {
  const { user } = useAuth()
  const wallet = user?.wallets[0]?.address
  return (
    <header className="topnav">
      <Link to="/app/portfolio" aria-label="Chainfolio home" style={{ textDecoration: 'none' }}><Logo size={30} /></Link>
      <nav className="topnav-links" aria-label="Main">
        {TABS.map(t => <NavLink key={t.to} to={t.to} className={({ isActive }) => `toplink${isActive ? ' active' : ''}`}>{t.label}</NavLink>)}
      </nav>
      <div style={{ marginLeft: 'auto' }} className="pill num">{wallet ? shortAddr(wallet) : 'No wallet'}</div>
    </header>
  )
}
