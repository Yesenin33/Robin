import { useCallback, useEffect, useRef, useState } from 'react'

export type AsyncState<T> =
  | { status: 'loading'; data?: T }
  | { status: 'ready'; data: T }
  | { status: 'error'; error: unknown; data?: T }

/** Runs fn on mount / when deps change; `reload(soft)` keeps showing old data while refetching. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [state, setState] = useState<AsyncState<T>>({ status: 'loading' })
  const seq = useRef(0)

  const run = useCallback(async (soft = false) => {
    const id = ++seq.current
    setState(s => (soft && s.data !== undefined ? s : { status: 'loading' }))
    try {
      const data = await fn()
      if (id === seq.current) setState({ status: 'ready', data })
    } catch (error) {
      if (id === seq.current) setState(s => ({ status: 'error', error, data: s.data }))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => { void run() }, [run])
  return { state, reload: run }
}
