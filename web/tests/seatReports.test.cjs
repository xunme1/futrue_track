const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const { test } = require('node:test')
const vm = require('node:vm')
const ts = require('typescript')

function loadSource(name, globals = {}) {
  const source = readFileSync(path.join(__dirname, '../src', name), 'utf8')
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText
  const exports = {}
  vm.runInNewContext(output, { exports, ...globals })
  return exports
}

const { seatHtmlUrl, latestSeatDate, refreshedSeatDate } = loadSource('seatReports.ts')
const report = (date, html_hash = 'a') => ({ date, html_hash, has_html: true, has_image: true })

test('same-day content version changes the shared URL; unchanged bytes stay stable', () => {
  const old = { ...report('20261021'), generated_at: 'first' }
  assert.equal(seatHtmlUrl(old), '/api/seat/20261021/html?v=a')
  assert.notEqual(seatHtmlUrl({ ...old, html_hash: 'b' }), seatHtmlUrl(old))
  assert.equal(seatHtmlUrl({ ...old, generated_at: 'later' }), seatHtmlUrl(old))
})

test('older API responses fall back to an encoded timestamp or an unversioned URL', () => {
  const old = { ...report('20261020', null), generated_at: '2026-10-20T18:05:00+08:00' }
  assert.equal(seatHtmlUrl(old), '/api/seat/20261020/html?v=2026-10-20T18%3A05%3A00%2B08%3A00')
  assert.equal(seatHtmlUrl(report('20261020', null)), '/api/seat/20261020/html')
})

test('opening selects the latest valid HTML with image-only fallback', () => {
  const imageOnly = { ...report('20261022'), has_html: false }
  assert.equal(latestSeatDate([imageOnly, report('20261021')]), '20261021')
  assert.equal(latestSeatDate([imageOnly]), '20261022')
  assert.equal(latestSeatDate([]), null)
  assert.equal(refreshedSeatDate('20261020', [report('20261021')], null), '20261021')
})

test('refresh follows the latest date but preserves a selected historical report', () => {
  const items = [report('20261022'), report('20261021', 'b'), report('20261020')]
  assert.equal(refreshedSeatDate('20261021', items, '20261021'), '20261022')
  assert.equal(refreshedSeatDate('20261020', items, '20261021'), '20261020')
  assert.equal(refreshedSeatDate('20261019', items, '20261021'), '20261022')
  assert.equal(refreshedSeatDate('20261021', [], '20261021'), null)
  assert.equal(refreshedSeatDate('20261021', items.slice(1), '20261021'), '20261021')
})

test('seat list explicitly bypasses cache and forwards cancellation without affecting other APIs', async () => {
  const requests = []
  const api = loadSource('api.ts', { fetch: async (...args) => {
    requests.push(args)
    return { ok: true, json: async () => [] }
  } })
  const controller = new AbortController()
  await api.fetchSeatList(controller.signal)
  assert.equal(requests[0][0], '/api/seat')
  assert.equal(requests[0][1].cache, 'no-store')
  assert.equal(requests[0][1].signal, controller.signal)
  await api.fetchContracts('1d')
  assert.equal(requests[1][1], undefined)
})
