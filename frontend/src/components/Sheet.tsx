import { useEffect, useRef, type ReactNode } from 'react'

/** The single bottom sheet used for every confirmation (swap, anything that needs a yes). */
export function Sheet({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null
    ref.current?.focus()
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', key)
    return () => { window.removeEventListener('keydown', key); prev?.focus?.() }
  }, [onClose])
  return (
    <>
      <div className="sheet-scrim" onClick={onClose} />
      <div className="sheet" role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref}>
        <div className="sheet-grab" />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <div className="sheet-title">{title}</div>
          <button onClick={onClose} aria-label="Close" className="btn btn-ghost"
            style={{ width: 44, height: 44, minHeight: 44, padding: 0, borderRadius: 99, fontSize: 18 }}>✕</button>
        </div>
        {children}
      </div>
    </>
  )
}
