export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }} aria-busy>
      <div className="skel" style={{ width: 120, height: 16, borderRadius: 8 }} />
      <div className="skel" style={{ width: 220, height: 44, borderRadius: 12 }} />
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="card" style={{ height: 72, animation: 'cfpulse 1.4s ease-in-out infinite' }} />
      ))}
    </div>
  )
}

export function ErrorState({ body, onRetry }: { body: string; onRetry: () => void }) {
  return (
    <div className="center-state">
      <div className="state-icon" style={{ background: 'var(--red-soft)' }}>⚠️</div>
      <div style={{ fontWeight: 800, fontSize: 20 }}>Couldn't load this</div>
      <div style={{ color: 'var(--muted)', fontSize: 15, lineHeight: 1.5 }}>{body} Your money is safe.</div>
      <button className="btn btn-white btn-sm" style={{ marginTop: 8 }} onClick={onRetry}>Try again</button>
    </div>
  )
}

export function EmptyState({ emoji, title, body, cta, onCta }: {
  emoji: string; title: string; body: string; cta?: string; onCta?: () => void
}) {
  return (
    <div className="center-state">
      <div className="state-icon card">{emoji}</div>
      <div style={{ fontWeight: 800, fontSize: 20 }}>{title}</div>
      <div style={{ color: 'var(--muted)', fontSize: 15, lineHeight: 1.5 }}>{body}</div>
      {cta && <button className="btn btn-primary btn-sm" style={{ marginTop: 8 }} onClick={onCta}>{cta}</button>}
    </div>
  )
}
