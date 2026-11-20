import { computed, reactive, ref } from 'vue'
import { ReadingSessionMachine, RecoverableSessionError } from '../services/readingSessionMachine'

export function useReadingSession({ api, initialSessionId = null, childProfileId, storyVersionId }) {
  const state = reactive({
    session: null,
    events: [],
    report: null,
    pending: false,
    lastRequestId: null,
  })
  const loading = ref(false)
  const error = ref(null)
  const notice = ref('')
  const lastAgentResult = ref(null)
  const machine = new ReadingSessionMachine({
    api,
    onChange(snapshot) {
      state.session = snapshot.session
      state.events = snapshot.events
      state.report = snapshot.report
      state.pending = snapshot.pending
      state.lastRequestId = snapshot.lastRequestId
    },
  })

  const status = computed(() => state.session?.status ?? 'CREATED')
  const revision = computed(() => state.session?.revision ?? 0)

  function clearMessages() {
    error.value = null
    notice.value = ''
  }

  function describeError(cause) {
    if (cause instanceof RecoverableSessionError) {
      notice.value = cause.message
      return
    }
    error.value = cause ?? new Error('请求失败')
  }

  async function initialize() {
    loading.value = true
    clearMessages()
    try {
      if (initialSessionId) await machine.restore(initialSessionId)
      else await machine.create({ childProfileId, storyVersionId })
      return state.session
    } catch (cause) {
      describeError(cause)
      throw cause
    } finally {
      loading.value = false
    }
  }

  async function start() {
    clearMessages()
    try {
      return await machine.start()
    } catch (cause) {
      describeError(cause)
      throw cause
    }
  }

  async function changePage(pageNumber) {
    clearMessages()
    try {
      return await machine.changePage(pageNumber)
    } catch (cause) {
      describeError(cause)
      throw cause
    }
  }

  async function finish() {
    clearMessages()
    try {
      return await machine.finish()
    } catch (cause) {
      describeError(cause)
      throw cause
    }
  }

  async function guidance(feedback) {
    clearMessages()
    try {
      const output = await machine.guidance(feedback)
      lastAgentResult.value = output.result
      return output.result
    } catch (cause) {
      describeError(cause)
      throw cause
    }
  }

  async function askQuestion(inputText) {
    clearMessages()
    try {
      const output = await machine.askQuestion(inputText)
      lastAgentResult.value = output.result
      return output.result
    } catch (cause) {
      describeError(cause)
      throw cause
    }
  }

  async function restore() {
    if (!state.session) return null
    clearMessages()
    try {
      return await machine.restore(state.session.readingSessionId)
    } catch (cause) {
      describeError(cause)
      throw cause
    }
  }

  return {
    state,
    loading,
    error,
    notice,
    status,
    revision,
    lastAgentResult,
    initialize,
    start,
    changePage,
    finish,
    guidance,
    askQuestion,
    restore,
  }
}

