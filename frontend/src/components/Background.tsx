const particles = Array.from({ length: 18 }, (_, i) => ({ x: (i * 37) % 100, s: 2 + (i % 3) * 2, d: 7 + (i % 5) * 2, dl: -(i * 1.3) }))

/** Restrained backdrop: two soft glows, a perspective grid and a few rising particles (hidden on phones). */
export function Background() {
  return (
    <div className="bg" aria-hidden>
      <div className="bg-blob1" />
      <div className="bg-blob2" />
      <div className="bg-grid" />
      {particles.map((p, i) => (
        <div key={i} className="bg-particle" style={{
          left: `${p.x}%`, width: p.s, height: p.s, animation: `cfrise ${p.d}s linear ${p.dl}s infinite`,
        }} />
      ))}
      <div className="bg-vignette" />
    </div>
  )
}
