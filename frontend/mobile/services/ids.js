export function createClientEventId(prefix = 'event') {
  const id = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
  return `${prefix}-${id}`.slice(0, 64)
}

export function createRequestKey(prefix = 'req') {
  return createClientEventId(prefix)
}
