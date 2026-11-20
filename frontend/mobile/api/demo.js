import { createClientEventId } from '../services/ids'

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

export class DemoReadingSessionsApi {
  constructor({ book, latency = 420 }) {
    this.book = book
    this.latency = latency
    this.session = null
    this.events = []
    this.promptByPage = new Map()
  }

  async create({ childProfileId = 1, storyVersionId = this.book.storyVersionId } = {}) {
    await wait(this.latency)
    this.session = {
      readingSessionId: 900001,
      storyVersionId,
      childProfileId,
      currentPage: 1,
      visitedPageNumbers: [],
      mode: 'PARENT_CHILD',
      status: 'CREATED',
      revision: 0,
      expiresAt: null,
    }
    this.events = []
    return result(this.session)
  }

  async get() {
    await wait(this.latency)
    return result({ session: { ...this.session }, events: [...this.events], report: null })
  }

  async addEvent(_sessionId, payload) {
    await wait(this.latency)
    assertRevision(this.session, payload.expectedRevision)
    if (payload.eventType === 'STARTED') {
      if (this.session.status !== 'CREATED' || payload.pageNumber !== 1) {
        throw demoError('SESSION_CONFLICT', '当前状态不允许开始阅读', 409)
      }
      this.session.status = 'IN_PROGRESS'
    } else if (payload.eventType === 'PAGE_CHANGED') {
      if (this.session.status !== 'IN_PROGRESS' || payload.pageNumber < 1 || payload.pageNumber > this.book.pages.length) {
        throw demoError('SESSION_CONFLICT', '当前页面不允许翻到目标页', 409)
      }
    } else if (payload.eventType === 'ANSWER') {
      if (this.session.status !== 'IN_PROGRESS' || payload.pageNumber !== this.session.currentPage) {
        throw demoError('SESSION_CONFLICT', '请在当前页回答', 409)
      }
    } else {
      throw demoError('VALIDATION_ERROR', '演示接口不支持该事件类型', 422)
    }
    this.session.currentPage = payload.pageNumber
    this.session.visitedPageNumbers = [...new Set([...this.session.visitedPageNumbers, payload.pageNumber])]
    this.session.revision += 1
    const event = {
      interactionEventId: this.events.length + 800,
      eventType: payload.eventType,
      ...(payload.eventType === 'ANSWER' ? { inputText: payload.inputText } : {}),
      pageNumber: payload.pageNumber,
    }
    this.events.push(event)
    return result({ interactionEventId: event.interactionEventId, revision: this.session.revision })
  }

  async submitGuidance(_sessionId, payload) {
    await wait(this.latency)
    assertRevision(this.session, payload.expectedRevision)
    const page = this.book.pages[payload.pageNumber - 1]
    if (!page) throw demoError('RESOURCE_NOT_FOUND', '页面不存在', 404)
    if (payload.feedback === 'INITIAL') {
      const outputText = page.prompt || '看看画面里发生了什么，再请孩子说一说。'
      this.promptByPage.set(payload.pageNumber, outputText)
      return this._agentResult(outputText, 'CONTEXT_QUESTION', 'GENERATED')
    }
    if (!this.promptByPage.has(payload.pageNumber)) {
      throw demoError('PROMPT_REQUIRED', '请先生成本页共读问题', 409)
    }
    if (payload.feedback === 'SKIPPED') {
      return this._agentResult('', 'SKIP', 'FALLBACK')
    }
    if (payload.feedback === 'NOT_UNDERSTOOD') {
      return this._agentResult(
        page.promptHint || '先看看谁在画面里，他在做什么？',
        'CONCRETE_RECALL',
        'GENERATED',
      )
    }
    if (payload.feedback === 'UNDERSTOOD') {
      return this._agentResult(
        '孩子的回答已由家长确认。演示模式不会保存录音或把转写文本写入后端。',
        'CONTEXT_QUESTION',
        'FALLBACK',
      )
    }
    throw demoError('VALIDATION_ERROR', '未知反馈类型', 422)
  }

  async submitQuestion(_sessionId, payload) {
    await wait(this.latency)
    assertRevision(this.session, payload.expectedRevision)
    this.promptByPage.set(payload.pageNumber, payload.inputText)
    return this._agentResult(`演示问题已记录：${payload.inputText}`, 'CONTEXT_QUESTION', 'FALLBACK')
  }

  async finish(_sessionId, payload) {
    await wait(this.latency)
    assertRevision(this.session, payload.expectedRevision)
    this.session.status = 'FINISHED'
    this.session.revision += 1
    const report = {
      readingSessionId: this.session.readingSessionId,
      summary: `本次演示阅读访问了 ${this.session.visitedPageNumbers.length} 页。未生成能力评价或内容安全检查结论。`,
      activities: [],
      status: 'READY',
    }
    return result({ session: { ...this.session }, report })
  }

  _agentResult(outputText, strategyType, status) {
    this.session.revision += 1
    return result({
      interactionEventId: this.events.length + 900,
      revision: this.session.revision,
      outputText,
      strategyType,
      status,
      sources: [],
    })
  }
}

export class DemoVoiceApi {
  async transcribe() {
    await wait(700)
    return result({
      text: '',
      status: 'RECOGNITION_UNAVAILABLE',
      reliable: false,
      message: '浏览器演示模式没有真实语音转写服务，请使用手动输入；实机模式会走 G08。',
    })
  }

  async synthesizeInteraction() {
    throw demoError('DEMO_AUDIO_UNAVAILABLE', '演示模式未接入回答音频合成', 503)
  }

  async synthesizePage() {
    throw demoError('DEMO_AUDIO_UNAVAILABLE', '演示模式使用本地页朗读音频', 503)
  }
}

function result(data) {
  return { data, requestId: `req-demo-${createClientEventId('id').slice(-12)}`, status: 200 }
}

function assertRevision(session, revision) {
  if (!session || session.revision !== revision) {
    throw demoError('SESSION_CONFLICT', '会话状态已变化', 409)
  }
}

function demoError(code, message, status) {
  const error = new Error(message)
  error.code = code
  error.status = status
  error.retryable = false
  return error
}
