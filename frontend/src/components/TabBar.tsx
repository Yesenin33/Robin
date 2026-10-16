import { NavLink } from 'react-router-dom'

export const TABS = [
  { to: '/app/portfolio', label: 'Portfolio', d: 'M21 12A9 9 0 1 1 12 3v9z' },
  { to: '/app/safety', label: 'Safety', d: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z M9 12l2 2 4-4' },
  { to: '/app/copy', label: 'Copy', d: 'M8 8h12v12H8z M4 16V4h12' },
  { to: '/app/profile', label: 'Profile', d: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M4 21c0-4 4-6 8-6s8 2 8 6' },
]

const Icon = ({ d, size }: { d: string; size: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>
)

/** Phone / tablet navigation (the sidebar takes over from 1024 px). */
export function TabBar() {
  return (
    <nav className="tabbar" aria-label="Main">
      {TABS.map(t => (
        <NavLink key={t.to} to={t.to} className={({ isActive }) => `tab${isActive ? ' active' : ''}`}>
          <span className="pillbg"><Icon d={t.d} size={22} /></span>
          <span>{t.label}</span>
        </NavLink>
      ))}
    </nav>
  )
}

export { Icon as NavIcon }
