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

/** Progress that says what is happening (real stages when the server reports them), how long it has been, a
 *  measured estimate when we have one, and always a way out. */
export function ProgressState({ title, detail, elapsedS, onCancel, steps, current, etaS }: {
  title: string; detail: string; elapsedS: number; onCancel: () => void
  steps?: string[]; current?: number; etaS?: number | null
}) {
  const slow = etaS != null && elapsedS > etaS * 2 + 10
  return (
    <div className="center-state" role="status" aria-live="polite">
      <div className="spinner" />
      <div className="t-title">{title}</div>
      <div className="t-body" style={{ color: 'var(--muted)' }}>{detail}</div>
      {steps && (
        <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6, textAlign: 'left' }}>
          {steps.map((t, i) => {
            const done = current != null && i < current, on = i === current
            return (
              <li key={t} className="t-body" style={{ display: 'flex', gap: 10, alignItems: 'center', color: on ? 'var(--text)' : done ? 'var(--green-text)' : 'var(--faint)', fontWeight: on ? 700 : 500 }}>
                <span aria-hidden style={{ width: 16, textAlign: 'center' }}>{done ? '✓' : on ? '›' : '·'}</span>{t}
              </li>
            )
          })}
        </ol>
      )}
      <div className="t-caption num">
        {elapsedS}s so far · {etaS != null ? `usually about ${etaS}s` : "we haven't timed this check yet"}
      </div>
      {slow && <div className="t-caption" style={{ color: 'var(--amber-text)' }}>This is taking longer than usual. The data sources are slow right now.</div>}
      <button className="btn btn-ghost btn-sm" style={{ marginTop: 8 }} onClick={onCancel}>Stop waiting</button>
    </div>
  )
}

export function ErrorState({ body, onRetry }: { body: string; onRetry: () => void }) {
  return (
    <div className="center-state">
      <div className="state-icon" style={{ background: 'var(--red-soft)' }}>⚠️</div>
      <div style={{ fontWeight: 800, fontSize: 20 }}>Couldn't load this</div>
      <div className="t-body" style={{ color: 'var(--muted)' }}>{body} Your money is safe.</div>
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
