/**
 * WalletConnect v2 via Reown AppKit, loaded on demand (the landing page stays light).
 * The modal offers installed (injected) wallets and WalletConnect: QR code on desktop, deep link into the
 * wallet app on phones. Nothing here custodies keys — it only yields an EIP-1193 provider for SIWE signing.
 */
import type { TokenOut } from '../api/types'
import { hasInjectedWallet, signInWithProvider, signInWithWallet, type Eip1193 } from './siwe'
import { telegramWebApp } from './telegram'

export const WC_PROJECT_ID = ((import.meta.env.VITE_WC_PROJECT_ID as string | undefined) ?? '').trim()
export const walletConnectEnabled = WC_PROJECT_ID.length > 0

const CHAIN_ID = Number(import.meta.env.VITE_CHAIN_ID ?? 4663)
const CHAIN_NAME = (import.meta.env.VITE_CHAIN_NAME as string | undefined) ?? 'Robinhood Chain'
const RPC_URL = (import.meta.env.VITE_RPC_URL as string | undefined) ?? 'https://rpc.mainnet.chain.robinhood.com'

export class WalletCancelled extends Error {
  code = 4001
  constructor() { super('You closed the wallet window.') }
}

let kit: Promise<{ modal: import('@reown/appkit').AppKit }> | null = null

function loadKit() {
  kit ??= (async () => {
    const [{ createAppKit }, { WagmiAdapter }, { defineChain }] = await Promise.all([
      import('@reown/appkit'), import('@reown/appkit-adapter-wagmi'), import('@reown/appkit/networks'),
    ])
    const chain = defineChain({
      id: CHAIN_ID,
      caipNetworkId: `eip155:${CHAIN_ID}`,
      chainNamespace: 'eip155',
      name: CHAIN_NAME,
      nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 },
      rpcUrls: { default: { http: [RPC_URL] } },
      blockExplorers: { default: { name: 'Blockscout', url: 'https://robinhoodchain.blockscout.com' } },
    })
    const adapter = new WagmiAdapter({ networks: [chain], projectId: WC_PROJECT_ID })
    const modal = createAppKit({
      adapters: [adapter],
      networks: [chain],
      defaultNetwork: chain,
      projectId: WC_PROJECT_ID,
      metadata: {
        name: 'Chainfolio',
        description: 'Your crypto portfolio on Robinhood Chain — simple and safe.',
        url: window.location.origin,
        icons: [`${window.location.origin}/icon.svg`],
      },
      features: { analytics: false, email: false, socials: false, onramp: false, swaps: false },
      themeMode: 'dark',
      themeVariables: { '--w3m-accent': '#00C805', '--w3m-font-family': 'Manrope, system-ui, sans-serif', '--w3m-z-index': 100 },
    })
    return { modal }
  })()
  // a failed load (offline, blocked CDN) must be retryable
  kit.catch(() => { kit = null })
  return kit
}

/** Open the AppKit modal and resolve with the connected wallet's EIP-1193 provider. */
async function connectWithModal(): Promise<Eip1193> {
  const { modal } = await loadKit()
  return new Promise<Eip1193>((resolve, reject) => {
    let done = false
    const finish = (fn: () => void) => { if (!done) { done = true; offAccount(); offState(); fn() } }
    const provider = () => modal.getWalletProvider() as Eip1193 | undefined
    const offAccount = modal.subscribeAccount(a => {
      const p = provider()
      if (a.isConnected && a.address && p) finish(() => { void modal.close(); resolve(p) })
    })
    const offState = modal.subscribeState(s => {
      // closed without connecting -> the user backed out (give a just-finished connect a moment to land)
      if (!s.open) setTimeout(() => finish(() => reject(new WalletCancelled())), 600)
    })
    const existing = provider()
    if (modal.getAddress() && existing) { finish(() => resolve(existing)); return }
    void modal.open({ view: 'Connect' })
  })
}

export const insideTelegram = () => telegramWebApp() !== null

/** What "Connect wallet" should do on this device. */
export function connectMode(): 'injected' | 'walletconnect' | 'unavailable' {
  if (hasInjectedWallet() && !insideTelegram()) return 'injected'
  return walletConnectEnabled ? 'walletconnect' : 'unavailable'
}

/**
 * Connect + sign in. `via: 'injected'` talks to window.ethereum directly (one tap, no modal);
 * `via: 'walletconnect'` opens AppKit (injected wallets are listed there too).
 */
export async function connectWallet(via: 'injected' | 'walletconnect', linkToken?: string | null): Promise<TokenOut> {
  if (via === 'injected') return signInWithWallet(linkToken)
  if (!walletConnectEnabled) throw new Error('WalletConnect is not set up yet. Try a browser wallet.')
  const provider = await connectWithModal()
  return signInWithProvider(provider, { strictChain: false, linkToken })
}

export async function disconnectWallet(): Promise<void> {
  if (!kit) return
  try { await (await kit).modal.disconnect() } catch { /* nothing connected */ }
}
