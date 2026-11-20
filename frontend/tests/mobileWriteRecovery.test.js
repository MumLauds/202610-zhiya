import test from 'node:test'
import assert from 'node:assert/strict'
import { requestJson } from '../mobile/api/client.js'
import { ReadingSessionMachine } from '../mobile/services/readingSessionMachine.js'

test('a lost ANSWER reply with unchanged progress can only retry the original event', async (t) => {
  const session = { readingSessionId: 7, currentPage: 2, status: 'IN_PROGRESS', revision: 4 }
  const writes = []
  const reads = []
  t.mock.method(globalThis, 'fetch', async (_path, { method, body }) => {
    if (method === 'GET') {
      reads.push(method)
      return Response.json({ data: { session, events: [], report: null } })
    }
    writes.push(JSON.parse(body))
    if (writes.length === 1) throw new TypeError('network disconnected')
    return Response.json({ data: { revision: 5 } })
  })
  const machine = new ReadingSessionMachine({
    initialSnapshot: { session },
    api: {
      addEvent: (id, body) => requestJson('/reading/' + id, { method: 'POST', body }),
      get: (id) => requestJson('/reading/' + id),
    },
  })
  await assert.rejects(machine.answer('我没听懂'), { code: 'REQUEST_OUTCOME_UNKNOWN' })
  assert.equal(machine.syncRequired, true)
  await assert.rejects(machine.answer('另一条回答'), /先重新核对/)
  assert.equal(writes.length, 1)
  await machine.reconcile()
  assert.equal(reads.length, 2)
  assert.deepEqual(writes[1], writes[0])
  assert.equal(writes[0].eventType, 'ANSWER')
  assert.equal(writes[0].inputText, '我没听懂')
  assert.equal(machine.session.revision, 5)
  assert.equal(machine.syncRequired, false)
})
