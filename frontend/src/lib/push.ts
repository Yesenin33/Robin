import { api } from '../api/client'

const b64ToBytes = (b64: string) => {
  const pad = '='.repeat((4 - (b64.length % 4)) % 4)
  const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from(raw, c => c.charCodeAt(0))
}

/** Ask permission, subscribe this browser with the server's VAPID key, register it with the API. */
export async function enablePush(): Promise<void> {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) throw new Error("This browser can't show alerts")
  if ((await Notification.requestPermission()) !== 'granted') throw new Error('Allow notifications to get alerts')
  const { public_key } = await api.pushKey()
  const reg = await navigator.serviceWorker.ready
  const sub = (await reg.pushManager.getSubscription())
    ?? await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(public_key) })
  await api.pushSubscribe(sub.toJSON())
}

export async function disablePush(): Promise<void> {
  const reg = await navigator.serviceWorker?.ready
  await (await reg?.pushManager.getSubscription())?.unsubscribe()
  await api.pushUnsubscribe()
}
