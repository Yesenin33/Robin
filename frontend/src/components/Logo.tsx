/** The Chainfolio mark (a "C" ring with a coin dot, on Robinhood green) + wordmark. Geometry = public/mark.svg. */
export function Mark({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 512 512" role="img" aria-label="Chainfolio" style={{ flex: 'none' }}>
      <rect width="512" height="512" rx="120" fill="#00C805" />
      <path d="M330.6 167.1A116 116 0 1 0 330.6 344.9" fill="none" stroke="#04210A" strokeWidth="58" strokeLinecap="round" />
      <circle cx="356" cy="256" r="31" fill="#04210A" />
    </svg>
  )
}

export function Logo({ size = 32 }: { size?: number }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: size * 0.3 }}>
      <Mark size={size} />
      <span style={{ fontWeight: 800, fontSize: size * 0.62, letterSpacing: '-.025em', color: 'var(--text)' }}>Chainfolio</span>
    </span>
  )
}
