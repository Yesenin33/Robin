import { getAddress } from 'viem'
import { api } from '../api/client'
import type { TokenOut } from '../api/types'

export type Eip1193 = { request: (a: { method: string; params?: unknown[] }) => Promise<any> }
declare global { interface Window { ethereum?: Eip1193 } }

/** EIP-55 checksummed form. Phantom rejects a lowercase `from` in personal_sign with "the address does not
 *  match the provided address for verification"; WalletConnect hands out lowercase addresses. */
function checksum(a: string): string {
  try { return getAddress(a) } catch { return a }
}

const CHAIN_ID = Number(import.meta.env.VITE_CHAIN_ID ?? 4663)
const CHAIN_HEX = '0x' + CHAIN_ID.toString(16)
const CHAIN_NAME = (import.meta.env.VITE_CHAIN_NAME as string | undefined) ?? 'Robinhood Chain'
const RPC_URL = (import.meta.env.VITE_RPC_URL as string | undefined) ?? 'https://rpc.mainnet.chain.robinhood.com'

/**
 * Nudge a wallet onto Robinhood Chain. Best-effort by design: the SIWE message names `Chain ID: 4663` and the
 * backend enforces it, and we never sign a transaction — so a wallet that cannot switch or add the chain
 * (Phantom answers an unknown chain with 4902; others throw a different code) must still be allowed to sign.
 * Blocking here was the single most likely way a real Phantom user could not get in.
 */
async function ensureChain(eth: Eip1193) {
  try {
    await eth.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: CHAIN_HEX }] })
  } catch {
    try {
      await eth.request({
        method: 'wallet_addEthereumChain',
        params: [{
          chainId: CHAIN_HEX,
          chainName: CHAIN_NAME,
          nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 },
          rpcUrls: [RPC_URL],
          blockExplorerUrls: ['https://robinhoodchain.blockscout.com'],
        }],
      })
    } catch { /* the wallet can't add it — signing still works, the message carries the chain */ }
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
 * Chain switching is best-effort: the SIWE message names chain 4663 itself, and we never send transactions.
 */
export async function signInWithProvider(
  eth: Eip1193, opts: { linkToken?: string | null } = {},
): Promise<TokenOut> {
  const accounts: string[] = await eth.request({ method: 'eth_requestAccounts' })
  const address = accounts?.[0]
  if (!address) throw new Error('Your wallet did not share an account.')
  await ensureChain(eth)
  const { nonce } = await api.siweNonce()
  const message = buildMessage(checksum(address), nonce)  // EIP-4361 wants the checksummed address
  const signature = await signMessage(eth, message, address)
  return api.siwe(message, signature, opts.linkToken ?? undefined)
}

/**
 * personal_sign across wallet quirks. Phantom's docs require the message HEX-encoded
 * (`0x${Buffer.from(message,'utf8').toString('hex')}`) and rejects a plain string with "the address does not
 * match the provided address for verification"; MetaMask accepts plain text; some wallets want the params in
 * the reverse order. The SIWE message itself is always the same — only the argument encoding changes — so we
 * try each shape in turn and let any of them sign. A user rejection stops immediately: never re-prompt.
 */
async function signMessage(eth: Eip1193, message: string, address: string): Promise<string> {
  const hex = '0x' + Array.from(new TextEncoder().encode(message))
    .map((b) => b.toString(16).padStart(2, '0')).join('')
  const from = checksum(address)
  const shapes: [string, string][] = [[hex, from], [message, from], [from, hex], [from, message]]
  let last: unknown
  for (const params of shapes) {
    try {
      const sig = await eth.request({ method: 'personal_sign', params })
      if (typeof sig === 'string' && sig.length > 2) return sig
    } catch (e) {
      last = e
      const code = (e as { code?: number } | null)?.code
      if (code === 4001 || code === 4100) throw e  // user rejected / not authorised: asking again is wrong
    }
  }
  throw last instanceof Error ? last : new Error('Your wallet did not sign the message.')
}

export const hasInjectedWallet = () => typeof window !== 'undefined' && !!window.ethereum

/** Injected-wallet sign-in (browser extension / in-app browser of a wallet). */
export async function signInWithWallet(linkToken?: string | null): Promise<TokenOut> {
  const eth = window.ethereum
  if (!eth) throw new Error('No wallet found. Install a browser wallet and try again.')
  return signInWithProvider(eth, { linkToken })
}
