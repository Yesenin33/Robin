const Bar = ({ w, h, r = 8 }: { w: number | string; h: number; r?: number }) => <div className="skel" style={{ width: w, height: h, borderRadius: r }} />

export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }} aria-busy aria-label="Loading">
      <Bar w={120} h={14} r={7} />
      <Bar w={240} h={56} r={12} />
      {Array.from({ length: rows }, (_, i) => <Bar key={i} w="100%" h={76} r={18} />)}
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
    <div className="card" role="status" aria-live="polite" style={{ padding: 28, borderRadius: 24, display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <div className="spinner" style={{ width: 32, height: 32, flex: 'none' }} />
        <div>
          <div className="t-title">{title}</div>
          <div className="num" style={{ fontSize: 14, color: 'var(--muted)', marginTop: 3 }}>
            {elapsedS}s so far · {etaS != null ? `usually about ${etaS}s` : "we haven't timed this check yet"}
          </div>
        </div>
      </div>
      <div style={{ fontSize: 15, color: 'var(--body)' }}>{detail}</div>
      {steps && (
        <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 14 }}>
          {steps.map((t, i) => {
            const done = current != null && i < current, on = i === current
            return (
              <li key={t} style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 16, fontWeight: on ? 700 : 500, color: !done && !on ? 'var(--faint)' : 'var(--ink)' }}>
                <span aria-hidden style={{ width: 22, height: 22, borderRadius: '50%', background: done ? 'var(--green)' : on ? 'var(--ink)' : '#CFCABD', color: 'var(--paper)', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 'none' }}>{done ? '✓' : ''}</span>{t}
              </li>
            )
          })}
        </ol>
      )}
      {slow && <div style={{ fontSize: 14, color: 'var(--amber-text)' }}>This is taking longer than usual. The data sources are slow right now.</div>}
      <button className="btn btn-ghost btn-sm" style={{ alignSelf: 'flex-start' }} onClick={onCancel}>Stop waiting</button>
    </div>
  )
}

export function ErrorState({ body, onRetry }: { body: string; onRetry: () => void }) {
  return (
    <div className="center-state">
      <div className="state-icon serif" style={{ background: 'var(--red-soft)', color: 'var(--red)', fontSize: 30 }}>!</div>
      <div className="serif" style={{ fontSize: 32, letterSpacing: '-.02em' }}>Couldn't load this</div>
      <div style={{ color: 'var(--body)', fontSize: 16, lineHeight: 1.5, maxWidth: 340 }}>{body} Your money is safe.</div>
      <button className="btn btn-primary" style={{ marginTop: 8, height: 52 }} onClick={onRetry}>Try again</button>
    </div>
  )
}

export function EmptyState({ emoji, title, body, cta, onCta }: {
  emoji: string; title: string; body: string; cta?: string; onCta?: () => void
}) {
  return (
    <div className="center-state">
      <div className="state-icon card" aria-hidden>{emoji}</div>
      <div className="serif" style={{ fontSize: 32, letterSpacing: '-.02em' }}>{title}</div>
      <div style={{ color: 'var(--body)', fontSize: 16, lineHeight: 1.5, maxWidth: 360 }}>{body}</div>
      {cta && <button className="btn btn-primary" style={{ marginTop: 8, height: 52 }} onClick={onCta}>{cta}</button>}
    </div>
  )
}
