const LOGO = (
  <svg width="110" height="110" viewBox="0 0 24 24" fill="rgba(4,33,10,.25)" stroke="#04210A" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
    <path d="M20.24 12.24a6 6 0 0 0-8.49-8.49L5 10.5V19h8.5z" /><path d="M16 8L2 22" /><path d="M17.5 15H9" />
  </svg>
)
const FACE = 'conic-gradient(from {d}deg,#00C805,#7CFFA0,#00A804,#B6FFC8,#00C805,#0B7A12,#5BFF7A,#00C805)'
const particles = Array.from({ length: 18 }, (_, i) => ({ x: (i * 37) % 100, s: 2 + (i % 3) * 2, d: 7 + (i % 5) * 2, dl: -(i * 1.3) }))
const rings = [
  { size: 900, border: '1px solid rgba(0,200,5,.35)', anim: 'cfring 16s linear infinite' },
  { size: 1200, border: '1px dashed rgba(124,255,160,.25)', anim: 'cfring 26s linear infinite reverse' },
  { size: 640, border: '2px solid rgba(0,200,5,.5)', anim: 'cfring 10s linear infinite', glow: true },
]

/** Animated backdrop from the design: blobs, perspective grid, orbit rings, particles, 3D coin. */
export function Background() {
  return (
    <div className="bg" aria-hidden>
      <div className="bg-blob1" />
      <div className="bg-blob2" />
      <div className="bg-grid" />
      <div className="bg-rings">
        {rings.map(r => (
          <div key={r.size} className="bg-ring" style={{
            width: r.size, height: r.size, margin: `${-r.size / 2}px 0 0 ${-r.size / 2}px`, border: r.border,
            animation: r.anim, boxShadow: r.glow ? '0 0 40px rgba(0,200,5,.4)' : undefined,
          }} />
        ))}
      </div>
      {particles.map((p, i) => (
        <div key={i} className="bg-particle" style={{
          left: `${p.x}%`, width: p.s, height: p.s, animation: `cfrise ${p.d}s linear ${p.dl}s infinite`,
        }} />
      ))}
      <div className="bg-coin-wrap">
        <div className="bg-coin">
          {[[-5, '#0A6B10'], [-2.5, '#0F8A17'], [0, '#12A01B'], [2.5, '#0F8A17'], [5, '#0A6B10']].map(([z, c]) => (
            <div key={z} style={{ transform: `translateZ(${z}px)`, background: c as string }} />
          ))}
          <div className="coin-face" style={{ transform: 'translateZ(7px)', background: FACE.replace('{d}', '20') }}>
            <div className="coin-inner" style={{ background: 'radial-gradient(circle at 30% 25%,rgba(255,255,255,.35),rgba(0,0,0,.15))' }}>{LOGO}</div>
          </div>
          <div className="coin-face" style={{ transform: 'rotateY(180deg) translateZ(7px)', background: FACE.replace('{d}', '200') }}>
            <div className="coin-inner" style={{ background: 'radial-gradient(circle at 70% 25%,rgba(255,255,255,.35),rgba(0,0,0,.15))' }}>{LOGO}</div>
          </div>
        </div>
      </div>
      <div className="bg-vignette" />
    </div>
  )
}
