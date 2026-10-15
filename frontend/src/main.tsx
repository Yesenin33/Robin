import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import { App } from './App'
import { AuthProvider } from './lib/auth'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)

if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => {
    // updateViaCache:'none' -> the browser always re-checks sw.js, so a deploy reaches users promptly
    void navigator.serviceWorker.register('/sw.js', { updateViaCache: 'none' }).then(r => r.update())
  })
  // A new worker took over (skipWaiting + clients.claim): reload once onto the new bundle
  // (not on the very first visit, when there was no previous worker)
  const hadController = !!navigator.serviceWorker.controller
  let reloaded = false
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (hadController && !reloaded) { reloaded = true; window.location.reload() }
  })
}
