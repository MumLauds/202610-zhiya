import { requestJson } from './client'

export class VoiceApi {
  constructor(request = requestJson) {
    this.request = request
  }

  transcribe({ file, readingSessionId, pageNumber, revision, signal }) {
    const form = new FormData()
    form.append('file', file, file.name || 'answer.webm')
    form.append('readingSessionId', String(readingSessionId))
    form.append('pageNumber', String(pageNumber))
    form.append('revision', String(revision))
    return this.request('/api/v1/voice/transcriptions', {
      method: 'POST',
      body: form,
      signal,
      timeoutMs: 30000,
    })
  }

  synthesizePage({ readingSessionId, pageNumber, revision, signal }) {
    return this.request('/api/v1/voice/syntheses', {
      method: 'POST',
      body: { readingSessionId, pageNumber, revision, preload: true },
      signal,
      timeoutMs: 30000,
    })
  }

  synthesizeInteraction({ interactionEventId, signal }) {
    return this.request('/api/v1/voice/syntheses', {
      method: 'POST',
      body: { interactionEventId },
      signal,
      timeoutMs: 30000,
    })
  }
}
