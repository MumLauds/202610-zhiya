import { createClientEventId } from '../api/client'

const keys = new Map()

export function readingCreationKey(accountId, profileId, versionId) {
  const slot = `zhiya.readingCreation.${accountId}.${profileId}.${versionId}`
  let saved
  try { saved = sessionStorage.getItem(slot) } catch { /* storage can be unavailable */ }
  const existing = saved || keys.get(slot)
  const requestId = existing || createClientEventId('create-reading')
  keys.set(slot, requestId)
  try { sessionStorage.setItem(slot, requestId) } catch { /* the current page can still retry */ }
  return {
    requestId,
    isRecovery: Boolean(existing),
    clear() {
      keys.delete(slot)
      try { sessionStorage.removeItem(slot) } catch { /* the in-memory entry is gone */ }
    },
  }
}
