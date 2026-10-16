import assert from 'node:assert/strict'
import { test } from 'node:test'

import { freshRecent } from './recent.ts'
import { formatUnits, isRejection, parseUnits, pctText } from './swapMath.ts'

const A = '0x' + 'ab'.repeat(20)

test('recent chips expire after 24 h and drop junk', () => {
  const now = 1_000_000_000_000
  const list = [{ ca: A, sym: 'ETH', ts: now - 3600_000 }, { ca: A.replace('ab', 'cd'), sym: 'OLD', ts: now - 25 * 3600_000 },
    { ca: 'nope', sym: 'X', ts: now }, { ca: A, sym: 'NOTS' }, null]
  assert.deepEqual(freshRecent(list, now).map(r => r.sym), ['ETH'])
  assert.deepEqual(freshRecent('garbage', now), [])
})

test('parseUnits is exact and rejects nonsense', () => {
  assert.equal(parseUnits('0.05', 18), 50_000_000_000_000_000n)
  assert.equal(parseUnits('1,5', 6), 1_500_000n)
  for (const bad of ['', '.', '0', '-1', 'abc', '1.1234567', '1e5']) assert.equal(parseUnits(bad, 6), null)
})

test('formatUnits never shows zero for a real amount', () => {
  assert.equal(formatUnits(1_500_000n, 6), '1.5')
  assert.equal(formatUnits('1000000000000000000', 18), '1')
  assert.notEqual(formatUnits(1n, 18), '0')
})

test('wallet rejection is recognised (and nothing else is)', () => {
  assert.equal(isRejection({ code: 4001 }), true)
  assert.equal(isRejection({ code: 'ACTION_REJECTED' }), true)
  assert.equal(isRejection({ code: -32603 }), false)
  assert.equal(isRejection(new Error('boom')), false)
})

test('slippage text', () => {
  assert.equal(pctText(50), '0.5%')
  assert.equal(pctText(100), '1%')
  assert.equal(pctText(300), '3%')
})
