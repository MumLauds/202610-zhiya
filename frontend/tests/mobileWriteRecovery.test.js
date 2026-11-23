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

test('a retryable post-commit ANSWER 503 keeps its receipt and reconciles the original event once', async (t) => {
  const session = {
    readingSessionId: 7, currentPage: 2, status: 'IN_PROGRESS', mode: 'PARENT_CHILD', revision: 4,
    visitedPageNumbers: [1, 2],
  }
  const saved = { interactionEventId: 12, eventType: 'ANSWER', pageNumber: 2, inputText: '雨伞', replyText: '一起看看画面。' }
  const replyText = '你找到了雨伞。它是什么颜色？'
  const followupQuestion = '它是什么颜色？'
  const writes = []
  let reads = 0
  t.mock.method(globalThis, 'fetch', async (_path, { method, body }) => {
    if (method === 'GET') {
      reads += 1
      return Response.json({ data: { session: { ...session, revision: 6 }, events: [saved] } })
    }
    writes.push(JSON.parse(body))
    if (writes.length === 1) {
      return Response.json({ error: { code: 'DEPENDENCY_UNAVAILABLE', retryable: true } }, { status: 503 })
    }
    return Response.json({ data: { interactionEventId: 12, revision: 5, replyText, followupQuestion } })
  })
  const machine = new ReadingSessionMachine({
    initialSnapshot: { session },
    api: {
      addEvent: (id, body) => requestJson('/reading/' + id, { method: 'POST', body }),
      get: (id) => requestJson('/reading/' + id),
    },
  })

  await assert.rejects(machine.answer('雨伞'), { code: 'DEPENDENCY_UNAVAILABLE' })
  assert.equal(reads, 1)
  assert.equal(machine.syncRequired, true)
  await assert.rejects(machine.answer('新的回答'), /先重新核对/)
  assert.equal(writes.length, 1)

  const recovered = await machine.reconcile()
  assert.equal(reads, 2)
  assert.deepEqual(writes, Array(2).fill(writes[0]))
  assert.equal(recovered.confirmedAnswer, '雨伞')
  assert.equal(machine.session.revision, 6)
  assert.equal(machine.syncRequired, false)
  assert.deepEqual(machine.events, [{ ...saved, revision: 5, replyText, followupQuestion }])
})
