export type Theme = 'light' | 'dark'
const KEY = 'cf_theme'

export const currentTheme = (): Theme =>
  document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light'

/** Light is the default. Dark is the same token set, opt-in, remembered in this browser. */
export function setTheme(t: Theme) {
  if (t === 'dark') document.documentElement.setAttribute('data-theme', 'dark')
  else document.documentElement.removeAttribute('data-theme')
  try { localStorage.setItem(KEY, t) } catch { /* private mode */ }
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', t === 'dark' ? '#0A0D0F' : '#FFFFFF')
}
