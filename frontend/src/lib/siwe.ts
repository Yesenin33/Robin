import { api } from '../api/client'
import type { TokenOut } from '../api/types'

export type Eip1193 = { request: (a: { method: string; params?: unknown[] }) => Promise<any> }
declare global { interface Window { ethereum?: Eip1193 } }

const CHAIN_ID = Number(import.meta.env.VITE_CHAIN_ID ?? 4663)
const CHAIN_HEX = '0x' + CHAIN_ID.toString(16)

async function ensureChain(eth: Eip1193) {
  try {
    await eth.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: CHAIN_HEX }] })
  } catch (e: any) {
    if (e?.code !== 4902) throw e
    await eth.request({
      method: 'wallet_addEthereumChain',
      params: [{
        chainId: CHAIN_HEX,
        chainName: import.meta.env.VITE_CHAIN_NAME ?? 'Robinhood Chain',
        nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 },
        rpcUrls: [import.meta.env.VITE_RPC_URL ?? 'https://rpc.chain.robinhood.com'],
      }],
    })
  }
}

/** EIP-4361 message — must match backend/app/services/siwe.py parser. */
export function buildMessage(address: string, nonce: string): string {
  const { host, origin } = window.location
  const issuedAt = new Date().toISOString()
  const expires = new Date(Date.now() + 10 * 60_000).toISOString()
  return `${host} wants you to sign in with your Ethereum account:\n${address}\n\n` +
    `Sign in to Chainfolio. This does not move any funds.\n\n` +
    `URI: ${origin}\nVersion: 1\nChain ID: ${CHAIN_ID}\nNonce: ${nonce}\n` +
    `Issued At: ${issuedAt}\nExpiration Time: ${expires}`
}

/**
 * Sign in with ANY EIP-1193 provider: an injected wallet or the WalletConnect session behind AppKit.
 * With a session token already stored, the backend attaches the wallet to THAT account (no second account);
 * `linkToken` does the same for a browser that has no session (Telegram Mini App -> "open in browser").
 * `strictChain: false` (WalletConnect) lets a wallet that can't switch chains still sign: the SIWE message
 * names chain 4663 itself, and we never send transactions.
 */
export async function signInWithProvider(
  eth: Eip1193, opts: { strictChain?: boolean; linkToken?: string | null } = {},
): Promise<TokenOut> {
  const accounts: string[] = await eth.request({ method: 'eth_requestAccounts' })
  const address = accounts?.[0]
  if (!address) throw new Error('Your wallet did not share an account.')
  try {
    await ensureChain(eth)
  } catch (e) {
    if (opts.strictChain !== false) throw e
  }
  const { nonce } = await api.siweNonce()
  const message = buildMessage(address, nonce)
  const signature = await eth.request({ method: 'personal_sign', params: [message, address] })
  return api.siwe(message, signature, opts.linkToken ?? undefined)
}

export const hasInjectedWallet = () => typeof window !== 'undefined' && !!window.ethereum

/** Injected-wallet sign-in (browser extension / in-app browser of a wallet). */
export async function signInWithWallet(linkToken?: string | null): Promise<TokenOut> {
  const eth = window.ethereum
  if (!eth) throw new Error('No wallet found. Install a browser wallet and try again.')
  return signInWithProvider(eth, { strictChain: true, linkToken })
}
