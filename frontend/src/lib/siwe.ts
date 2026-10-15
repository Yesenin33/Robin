import { api } from '../api/client'
import type { TokenOut } from '../api/types'

type Eip1193 = { request: (a: { method: string; params?: unknown[] }) => Promise<any> }
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

export async function signInWithWallet(): Promise<TokenOut> {
  const eth = window.ethereum
  if (!eth) throw new Error('No wallet found. Install a browser wallet and try again.')
  const [address] = await eth.request({ method: 'eth_requestAccounts' })
  await ensureChain(eth)
  const { nonce } = await api.siweNonce()
  const message = buildMessage(address, nonce)
  const signature = await eth.request({ method: 'personal_sign', params: [message, address] })
  return api.siwe(message, signature)
}
