import { Link, NavLink } from 'react-router-dom'

import { useAuth } from '../lib/auth'
import { shortAddr } from '../lib/format'
import { Logo } from './Logo'
import { NavIcon, TABS } from './TabBar'

/** Desktop navigation (>= 1024 px): dark sidebar with the wallet at the bottom. */
export function Sidebar() {
  const { user } = useAuth()
  const wallet = user?.wallets[0]?.address
  return (
    <aside className="sidebar">
      <Link to="/" className="side-brand" aria-label="Chainfolio home"><Logo size={32} onDark /></Link>
      <nav className="side-nav" aria-label="Main">
        {TABS.map(t => (
          <NavLink key={t.to} to={t.to} className={({ isActive }) => `side-link${isActive ? ' active' : ''}`}>
            <NavIcon d={t.d} size={20} />{t.label}
          </NavLink>
        ))}
      </nav>
      <Link to="/app/profile" className="side-wallet">
        <div className="avatar" style={{ width: 32, height: 32, background: 'var(--lime)', fontSize: 13 }}>0x</div>
        <div style={{ minWidth: 0 }}>
          <div className="num" style={{ fontSize: 14, fontWeight: 600 }}>{wallet ? shortAddr(wallet) : 'No wallet'}</div>
          <div style={{ fontSize: 12, color: '#B9B3A6', marginTop: 1 }}>Robinhood Chain</div>
        </div>
      </Link>
    </aside>
  )
}
