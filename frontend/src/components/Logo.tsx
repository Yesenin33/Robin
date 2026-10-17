/** The Chainfolio mark: a lime "C" ring with a rising line, on ink. Geometry = public/mark.svg. */
export function Mark({ size = 32, onDark = false }: { size?: number; onDark?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" role="img" aria-label="Chainfolio" style={{ flex: 'none' }}>
      <rect width="32" height="32" rx="9" fill={onDark ? '#1E1A10' : '#110E08'} />
      <path d="M22 10.5A8.5 8.5 0 1 0 22 21.5" fill="none" stroke="#CCFF00" strokeWidth="3" strokeLinecap="round" />
      <path d="M14 19l4-4 2 2 5-5.5" fill="none" stroke="#F7F5EF" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function Logo({ size = 32, onDark = false }: { size?: number; onDark?: boolean }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
      <Mark size={size} onDark={onDark} />
      <span style={{ fontFamily: 'var(--serif)', fontSize: size * 0.75, letterSpacing: '-.02em', color: onDark ? '#F7F5EF' : 'var(--ink)' }}>Chainfolio</span>
    </span>
  )
}
