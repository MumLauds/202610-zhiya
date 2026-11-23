<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import InteractionCard from './InteractionCard.vue'
import PageAudioPlayer from './PageAudioPlayer.vue'
import ReadingText from './ReadingText.vue'
import ReadingSummary from './ReadingSummary.vue'
import VoiceAnswerDialog from './VoiceAnswerDialog.vue'
import { useAudioController } from '../composables/useAudioController'
import { useReadingSession } from '../composables/useReadingSession'
import { approvedBranchesAt } from '../../src/data/readingBranches'
import { latestReadingQuestion } from '../../src/data/readingQuestions'

const props = defineProps({
  book: { type: Object, required: true },
  mode: { type: String, default: 'demo' },
  sessionApi: { type: Object, required: true },
  voiceApi: { type: Object, required: true },
  childProfileId: { type: Number, default: 1 },
  storyVersionId: { type: Number, required: true },
  initialSessionId: { type: Number, default: null },
  initialSnapshot: { type: Object, default: null },
})
const emit = defineEmits(['exit', 'finished', 'session'])

const session = useReadingSession({
  api: props.sessionApi,
  initialSessionId: props.initialSessionId,
  initialSnapshot: props.initialSnapshot,
  childProfileId: props.childProfileId,
  storyVersionId: props.storyVersionId,
})

const showPinyin = ref(true)
const highlightIndex = ref(-1)
const interactionResult = ref(null)
const questionReady = computed(() => {
  const result = displayedQuestion.value
  return Boolean(result?.outputText?.trim()) && result.status !== 'BLOCKED' &&
    !['SKIP', 'SAFETY_BLOCK'].includes(result.strategyType) && result.feedback !== 'SKIPPED'
})
const showAnswerDialog = ref(false)
const asking = ref(false)
const storyAnswer = computed(() => session.state.events.findLast(event =>
  event.eventType === 'ASK' && event.pageNumber === currentPageNumber.value && event.outputText))
const lastAnswerText = ref('')
const answerDraft = ref('')
const pageAudioSrc = ref('')
const pagePlayer = ref(null)
const pageAudioLoading = ref(false)
const answerAudioUrl = ref('')
const answerAudioLoading = ref(false)
const answerAudioNotice = ref('')
const pendingFeedback = ref(null)
const foregroundBusy = ref(false)
const childAuthorizationExpired = ref(false)
let autoQuestionPage = ''
let lastOptionsVisit = ''
let answerAudioEventId = null
const answerAudio = useAudioController((url) => {
  if (answerAudioUrl.value === url) {
    answerAudioUrl.value = ''
    answerAudioNotice.value = '回答音频已不可用，可再次点击播放以重新准备。'
  }
})
let pageAudioExpiryTimer
let pageAudioRequest = null
let playWhenPrepared = false
let answerAudioExpiresAt = NaN
let answerAudioRequest = null
let requestGeneration = 0
let pageRequestGeneration = 0
let active = true

const visited = computed(() => session.state.session?.visitedPageNumbers ?? [])
const historyIndex = ref(null)
const lastVisitIndex = computed(() => visited.value.length - 1)
const reviewing = computed(() => historyIndex.value !== null && historyIndex.value < lastVisitIndex.value)
const visitIndex = computed(() => reviewing.value ? historyIndex.value : lastVisitIndex.value)
const currentPageNumber = computed(() => reviewing.value ? visited.value[visitIndex.value]
  : (session.state.session?.currentPage ?? props.book.pages[0]?.page ?? 1))
const isChildMode = computed(() => props.mode === 'live' && session.state.session?.mode === 'CHILD_SOLO')
const isParentReading = computed(() => props.mode === 'live' && session.state.session?.mode === 'PARENT_CHILD')
const parentVisitIndex = computed(() => isParentReading.value ? lastVisitIndex.value : null)
const currentVisitEvents = computed(() => {
  const events = session.state.events
  if (!isParentReading.value) return events
  const boundary = events.findLastIndex((event) => ['STARTED', 'PAGE_CHANGED', 'BRANCH_CHOSEN'].includes(event.eventType))
  return events.slice(boundary + 1)
})
const currentPageEvents = computed(() => currentVisitEvents.value.filter(event => event.pageNumber === currentPageNumber.value))
const savedAnswer = computed(() => isParentReading.value && !reviewing.value
  ? currentPageEvents.value.findLast(event => event.eventType === 'ANSWER')
  : null)
const displayedQuestion = computed(() => {
  const settled = interactionResult.value
  if (!isParentReading.value || reviewing.value) return settled
  const guidance = !settled || isVisibleGuidance(settled) ? settled
    : currentPageEvents.value.findLast(event => isPageGuidance(event) && isVisibleGuidance(event))
  // Blank settled feedback is not a new question. Without an older prompt, it still prevents automatic resubmission.
  return latestReadingQuestion(currentPageEvents.value, guidance) ?? settled
})
const previousAnswer = computed(() => questionReady.value
  && displayedQuestion.value?.interactionEventId >= savedAnswer.value?.interactionEventId)
const answerFollowup = computed(() => displayedQuestion.value?.eventType === 'ANSWER' && previousAnswer.value)
const savedReply = computed(() => {
  const reply = savedAnswer.value?.replyText
  const question = savedAnswer.value?.followupQuestion
  return question && reply?.endsWith(question)
    ? reply.slice(0, -question.length).trimEnd() : reply
})
const confirmedAnswerText = computed(() => isParentReading.value ? savedAnswer.value?.inputText : lastAnswerText.value)
const childAnswerOptions = computed(() => interactionResult.value && !questionReady.value ? [] : session.state.answerOptions)

const currentPageIndex = computed(() => {
  const index = props.book.pages.findIndex((page) => page.page === currentPageNumber.value)
  return index
})
const currentPage = computed(() => props.book.pages[currentPageIndex.value])
const progressText = computed(() => `${currentPageIndex.value + 1} / ${props.book.pages.length}`)
const progressPercent = computed(() => ((currentPageIndex.value + 1) / props.book.pages.length) * 100)
const branchChoices = computed(() => props.mode === 'live' && !reviewing.value
  ? approvedBranchesAt(props.book.branches, currentPageNumber.value) : [])
const nextPageNumber = computed(() => {
  if (reviewing.value) return visited.value[visitIndex.value + 1]
  if (props.mode === 'live') return currentPage.value?.defaultNextPageNumber
  return props.book.pages[currentPageIndex.value + 1]?.page
})
const isLastPage = computed(() => !reviewing.value && !branchChoices.value.length && nextPageNumber.value == null)
const isFinished = computed(() => session.state.session?.status === 'FINISHED')
const pending = computed(() => session.state.pending || Boolean(pendingFeedback.value) || session.loading.value)
const navigationPending = computed(() => foregroundBusy.value || (pending.value && pendingFeedback.value !== 'INITIAL'))
const canSkip = computed(() => !foregroundBusy.value && !session.state.syncRequired && (pendingFeedback.value === 'INITIAL' ||
  (!pending.value && interactionResult.value?.strategyType !== 'SKIP' && interactionResult.value?.feedback !== 'SKIPPED')))
const revision = computed(() => session.state.session?.revision ?? 0)
const sessionId = computed(() => session.state.session?.readingSessionId ?? null)
const childDeadline = computed(() => {
  const current = session.state.session
  if (props.mode !== 'live' || current?.mode !== 'CHILD_SOLO') return null
  const deadline = Date.parse(current.expiresAt)
  return Number.isFinite(deadline) ? deadline : null
})
watch(childDeadline, (deadline, _previous, onCleanup) => {
  childAuthorizationExpired.value = false
  if (deadline === null || checkChildAuthorization()) return
  const timer = setTimeout(checkChildAuthorization, deadline - Date.now())
  onCleanup(() => clearTimeout(timer))
}, { immediate: true, flush: 'sync' })
watch(sessionId, (id) => { if (props.mode === 'live' && id) emit('session', id) })
const errorMessage = computed(() => session.error.value?.message ?? session.error.value ?? '')
const creationUnconfirmed = computed(() => !props.initialSessionId && !session.state.session && session.error.value?.code === 'REQUEST_OUTCOME_UNKNOWN')
const serverErrorHint = computed(() => (session.error.value?.status ?? 0) >= 500 ? '阅读服务暂不可用，请稍后重试。' : '')

watch([currentPageNumber, reviewing, parentVisitIndex], () => {
  resetInteractionForPage()
  restorePageQuestion()
})
watch(() => session.state.events, () => restorePageQuestion())
const activePageKey = computed(() => props.mode === 'live' && !childAuthorizationExpired.value && !reviewing.value && !session.state.syncRequired
  && session.state.session?.status === 'IN_PROGRESS' ? `${sessionId.value}:${currentPageNumber.value}:${parentVisitIndex.value ?? ''}` : '')
const textCanPlay = computed(() => !navigationPending.value && !showAnswerDialog.value
  && (activePageKey.value || (props.mode === 'demo' && pageAudioSrc.value)))
watch(activePageKey, (key) => {
  if (key && !pageAudioSrc.value) void loadPageAudio(true)
  else if (!key) resetAudio()
}, { immediate: true, flush: 'post' })
watch([activePageKey, pending, displayedQuestion], ([key, busy, question]) => {
  if (!key || busy || question || autoQuestionPage === key) return
  autoQuestionPage = key
  void submitFeedback('INITIAL', { background: true })
}, { immediate: true, flush: 'post' })
// Revisiting an existing question needs its current server options, without regenerating the question.
watch([activePageKey, lastVisitIndex, pending, questionReady, isChildMode, childAnswerOptions],
  ([key, index, busy, ready, child, options]) => {
    const visit = `${key}:${index}`
    if (!key || busy || !ready || !child || options !== null || lastOptionsVisit === visit) return
    lastOptionsVisit = visit
    void refreshChildOptions()
  }, { immediate: true, flush: 'post' },
)
onBeforeUnmount(() => {
  active = false
  cancelPageAudioRequest()
  stopAnswerAudio()
  clearTimeout(pageAudioExpiryTimer)
  pageRequestGeneration += 1
  requestGeneration += 1
})

onMounted(async () => {
  await initializeReader()
})

async function initializeReader() {
  try {
    await session.initialize()
    if (!active) return
    if (session.state.session?.status === 'CREATED') await session.start()
    if (props.mode === 'demo') pageAudioSrc.value = currentPage.value?.audio || ''
  } catch {
    // The composable surfaces the concrete API error in the page.
  }
}

async function retryInitialize() {
  pageAudioSrc.value = ''
  await initializeReader()
}

function stopAnswerAudio() {
  answerAudioRequest?.abort()
  answerAudioRequest = null
  answerAudioLoading.value = false
  answerAudio.stop()
}

function cancelPageAudioRequest() {
  playWhenPrepared = false
  pageAudioRequest?.abort()
  pageAudioRequest = null
  pageAudioLoading.value = false
}

function resetAudio() {
  pageRequestGeneration += 1
  clearTimeout(pageAudioExpiryTimer)
  cancelPageAudioRequest()
  stopAnswerAudio()
  answerAudioUrl.value = ''
  answerAudioEventId = null
  answerAudioNotice.value = ''
  pageAudioSrc.value = props.mode === 'demo' ? (currentPage.value?.audio ?? '') : ''
}

function checkChildAuthorization() {
  if (childDeadline.value === null || Date.now() < childDeadline.value) return false
  if (!childAuthorizationExpired.value) {
    childAuthorizationExpired.value = true
    pagePlayer.value?.stop()
    resetAudio()
    showAnswerDialog.value = false
  }
  return true
}

function resetInteractionForPage() {
  requestGeneration += 1
  resetAudio()
  interactionResult.value = null
  pendingFeedback.value = null
  lastAnswerText.value = ''
  answerDraft.value = ''
  showAnswerDialog.value = false
  highlightIndex.value = -1
}

function isPageGuidance(event) {
  return event.eventType === 'QUESTION' ||
    (event.eventType === 'FEEDBACK' && ['UNDERSTOOD', 'NOT_UNDERSTOOD', 'SKIPPED'].includes(event.feedback))
}

function isVisibleGuidance(event) {
  return Boolean(event?.outputText?.trim()) || event?.status === 'BLOCKED'
    || ['SKIP', 'SAFETY_BLOCK'].includes(event?.strategyType) || event?.feedback === 'SKIPPED'
}

function restorePageQuestion() {
  if (reviewing.value || session.state.session?.status !== 'IN_PROGRESS') return
  interactionResult.value = currentPageEvents.value.findLast((event) =>
    isPageGuidance(event) && (event.strategyType || event.outputText?.trim() || event.feedback === 'SKIPPED')) ?? null
}

async function syncReading() {
  try {
    const recovered = await session.restore()
    if (!active) return
    historyIndex.value = null
    restorePageQuestion()
    if (recovered?.confirmedAnswer) {
      if (!isParentReading.value) lastAnswerText.value = recovered.confirmedAnswer
      answerDraft.value = ''
    }
  } catch {
    // The read can be retried without sending a new event.
  }
}

async function retryStart() {
  if (pending.value) return
  await syncReading()
  if (!active || session.error.value || session.state.session?.status !== 'CREATED') return
  try { await session.start() } catch { /* the same session remains available for recovery */ }
}

async function goNext() {
  if (navigationPending.value || !session.state.session || branchChoices.value.length) return
  answerAudio.stop()
  if (isLastPage.value) {
    await finishReading()
    return
  }
  const next = nextPageNumber.value
  if (!props.book.pages.some((page) => page.page === next)) return
  if (reviewing.value) {
    const nextIndex = visitIndex.value + 1
    historyIndex.value = nextIndex < lastVisitIndex.value ? nextIndex : null
    return
  }
  foregroundBusy.value = true
  try {
    await session.changePage(next)
  } catch {
    // Conflict recovery is reported through `session.notice`.
  } finally {
    foregroundBusy.value = false
  }
}

async function chooseBranch(branchId) {
  if (navigationPending.value || session.state.syncRequired || !branchChoices.value.some(branch => branch.branchId === branchId)) return
  answerAudio.stop()
  foregroundBusy.value = true
  try {
    await session.chooseBranch(branchId)
  } catch {
    // Keep the same session and show its authoritative progress or error.
  } finally {
    foregroundBusy.value = false
  }
}

function goPrevious() {
  if (navigationPending.value || visitIndex.value <= 0) return
  answerAudio.stop()
  historyIndex.value = visitIndex.value - 1
}

async function finishReading() {
  if (navigationPending.value) return
  answerAudio.stop()
  foregroundBusy.value = true
  try {
    await session.finish()
    emit('finished', { book: props.book, session: session.state.session, report: session.state.report })
  } catch {
    // Error is rendered from the composable state.
  } finally {
    foregroundBusy.value = false
  }
}

async function submitFeedback(feedback, { background = false } = {}) {
  if (!session.state.session || reviewing.value || session.state.syncRequired) return
  if (feedback === 'SKIPPED' ? !canSkip.value : pending.value) return
  pendingFeedback.value = feedback
  const page = currentPageNumber.value
  const generation = ++requestGeneration
  try {
    const result = await session.guidance(feedback, { background })
    if (generation !== requestGeneration || page !== currentPageNumber.value) return
    interactionResult.value = result
    return result
  } catch (error) {
    if (generation === requestGeneration && error?.code === 'PROMPT_REQUIRED') interactionResult.value = null
  } finally {
    if (generation === requestGeneration) pendingFeedback.value = null
  }
}

async function refreshChildOptions() {
  if (!isChildMode.value || !activePageKey.value || pending.value) return
  try { await session.restore() } catch { /* Keep the question visible and offer a read-only retry. */ }
}

async function openAnswerDialog(question = false) {
  if (isChildMode.value || session.state.syncRequired) return
  if (pageAudioLoading.value) cancelPageAudioRequest()
  asking.value = question
  if (!question && !displayedQuestion.value) {
    const result = await submitFeedback('INITIAL')
    if (!result) return
  }
  if (question || questionReady.value) {
    cancelPageAudioRequest()
    pagePlayer.value?.stop()
    stopAnswerAudio()
    showAnswerDialog.value = true
  }
}

async function handleAnswerConfirmed({ text }) {
  if (isChildMode.value || (!asking.value && !questionReady.value)) return
  await submitAnswerText(text, asking.value)
}

async function selectChildAnswer(text) {
  if (!isChildMode.value || !childAnswerOptions.value?.includes(text)) return
  await submitAnswerText(text, false)
}

async function submitAnswerText(text, question) {
  if (pending.value || reviewing.value || session.state.syncRequired || checkChildAuthorization()) return
  if (!isChildMode.value) answerDraft.value = text
  const generation = requestGeneration
  const isCurrent = () => active && generation === requestGeneration
  try {
    if (question) await session.askQuestion(text)
    else {
      await session.answer(text)
      if (!isCurrent()) return
      if (!isParentReading.value) lastAnswerText.value = text
    }
    if (!isCurrent()) return
    answerDraft.value = ''
    showAnswerDialog.value = false
  } catch {
    // Keep a rejected draft on this visit; uncertain writes must use the original receipt.
    if (isCurrent() && (session.state.syncRequired || session.notice.value)) showAnswerDialog.value = false
  }
}

async function askShortQuestion(text) {
  if (navigationPending.value || reviewing.value || session.state.syncRequired) return
  if (pageAudioLoading.value) cancelPageAudioRequest()
  foregroundBusy.value = true
  try { await session.askQuestion(text) } catch { /* Error and reconciliation appear in the reader. */ }
  finally { foregroundBusy.value = false }
}

async function loadPageAudio(silent = false) {
  if (checkChildAuthorization() || !activePageKey.value || pageAudioLoading.value || showAnswerDialog.value) return
  const page = currentPageNumber.value
  const controller = new AbortController()
  pageAudioRequest = controller
  pageAudioLoading.value = true
  try {
    const response = await props.voiceApi.synthesizePage({
      readingSessionId: session.state.session.readingSessionId,
      pageNumber: page,
      revision: revision.value,
      signal: controller.signal,
    })
    if (controller.signal.aborted || page !== currentPageNumber.value || checkChildAuthorization()) return
    const { audioUrl, expiresAt } = response.data
    pageAudioSrc.value = audioUrl
    answerAudioNotice.value = ''
    clearTimeout(pageAudioExpiryTimer)
    const remaining = Date.parse(expiresAt) - Date.now()
    if (Number.isFinite(remaining)) {
      if (remaining <= 0) discardPageAudio(audioUrl)
      else pageAudioExpiryTimer = setTimeout(() => discardPageAudio(audioUrl), remaining)
    }
    if (playWhenPrepared && pageAudioSrc.value) {
      playWhenPrepared = false
      await nextTick()
      if (!controller.signal.aborted && page === currentPageNumber.value && textCanPlay.value) {
        await pagePlayer.value?.toggle()
      }
    }
  } catch (error) {
    if ((!silent || playWhenPrepared) && !controller.signal.aborted && page === currentPageNumber.value) {
      answerAudioNotice.value = error?.message ?? '页面朗读音频准备失败'
    }
  } finally {
    if (pageAudioRequest === controller) {
      playWhenPrepared = false
      pageAudioRequest = null
      pageAudioLoading.value = false
    }
  }
}

function playPageText() {
  if (!textCanPlay.value || checkChildAuthorization()) return
  if (pageAudioSrc.value) {
    void pagePlayer.value?.toggle()
    return
  }
  playWhenPrepared = true
  void loadPageAudio()
}

function discardPageAudio(url) {
  if (props.mode !== 'live' || url !== pageAudioSrc.value) return
  clearTimeout(pageAudioExpiryTimer)
  pageAudioSrc.value = ''
  answerAudioNotice.value = '本页音频已不可用，可重新准备朗读。'
}

async function playAnswerAudio(result = interactionResult.value) {
  if (checkChildAuthorization() || showAnswerDialog.value || !result?.outputText || result.strategyType === 'SAFETY_BLOCK') return
  answerAudioNotice.value = ''
  if (props.mode === 'demo') {
    answerAudioNotice.value = '演示模式没有回答音频；实机模式将在 G10 返回 interactionEventId 后请求 API-023。'
    return
  }
  const eventId = result.interactionEventId
  const matches = () => result.eventType === 'ASK'
    ? storyAnswer.value?.interactionEventId === eventId : result.revision === revision.value
  if (!eventId || answerAudioLoading.value || !matches()) {
    answerAudioNotice.value = '当前结果没有可用的 interactionEventId，无法请求回答音频。'
    return
  }
  const page = currentPageNumber.value
  const generation = pageRequestGeneration
  pagePlayer.value?.stop()
  const controller = new AbortController()
  answerAudioRequest = controller
  answerAudioLoading.value = true
  try {
    if (!answerAudioUrl.value || eventId !== answerAudioEventId || Date.now() >= answerAudioExpiresAt) {
      const response = await props.voiceApi.synthesizeInteraction({ interactionEventId: eventId, signal: controller.signal })
      if (controller.signal.aborted || generation !== pageRequestGeneration || page !== currentPageNumber.value
        || !matches() || checkChildAuthorization()) return
      answerAudioUrl.value = response.data.audioUrl
      answerAudioEventId = eventId
      answerAudioExpiresAt = Date.parse(response.data.expiresAt)
    }
    if (!await answerAudio.play({ url: answerAudioUrl.value, source: 'answer' }) && !controller.signal.aborted) {
      answerAudioNotice.value = answerAudio.error.value
    }
  } catch (error) {
    if (!controller.signal.aborted && generation === pageRequestGeneration && page === currentPageNumber.value) {
      answerAudioNotice.value = error?.message ?? '回答音频准备失败'
    }
  } finally {
    if (answerAudioRequest === controller) {
      answerAudioRequest = null
      answerAudioLoading.value = false
    }
  }
}

watch([() => interactionResult.value?.interactionEventId, () => storyAnswer.value?.interactionEventId], () => {
  stopAnswerAudio()
  answerAudioUrl.value = ''
})

function exitReading() {
  answerAudio.stop()
  emit('exit')
}
</script>

<template>
  <div class="reader-view">
    <header class="reader-topbar">
      <button class="topbar-back" type="button" aria-label="返回绘本馆" @click="exitReading">←</button>
      <div class="reader-title">
        <strong>{{ book.title }}</strong>
        <span>{{ progressText }}</span>
      </div>
      <div class="reader-badges">
        <span class="mode-tag" :class="mode === 'live' ? 'mode-live' : ''">{{ mode === 'demo' ? '演示' : '实机' }}</span>
        <span class="revision-tag">rev {{ revision }}</span>
      </div>
    </header>

    <div class="reader-progress" aria-hidden="true">
      <span :style="{ width: `${progressPercent}%` }" />
    </div>

    <main v-if="session.state.session" class="reader-content">
      <section v-if="childAuthorizationExpired" class="reader-error-card">
        <p role="alert">本次儿童阅读授权已到期，请交还家长。</p>
        <button type="button" class="button button-ghost" @click="exitReading">返回</button>
      </section>
      <section v-else-if="session.state.syncRequired" class="reader-error-card">
        <p>上次操作的结果仍待核对。{{ errorMessage }}</p>
        <button type="button" class="button button-primary" :disabled="pending" @click="syncReading">重新核对进度</button>
        <p>若服务器尚未记录上次操作，将用同一事件编号重试。</p>
      </section>
      <section v-else-if="session.state.session.status === 'CREATED'" class="reader-error-card">
        <p>{{ errorMessage || session.notice.value || '会话已创建，等待开始阅读。' }}</p>
        <button type="button" class="button button-primary" :disabled="pending" @click="retryStart">开始本次阅读</button>
      </section>
      <ReadingSummary
        v-else-if="isFinished"
        v-model:report="session.state.report"
        :book="book"
        :session="session.state.session"
        :session-api="mode === 'live' ? sessionApi : null"
        @exit="exitReading"
      />

      <p v-else-if="session.state.session.status !== 'IN_PROGRESS' || !currentPage || session.state.session.storyVersionId !== book.storyVersionId">本次会话或对应书页不可用，请返回绘本馆。</p>
      <template v-else>
        <article class="page-card">
          <div class="page-visual">
            <img :src="currentPage.image || book.cover" :alt="`${book.title} 第 ${currentPageIndex + 1} 页`" fetchpriority="high" />
            <span class="page-scene">{{ currentPage.scene || `第 ${currentPageIndex + 1} 幕` }}</span>
          </div>
          <div class="page-reading">
            <div v-if="currentPage.tokens.some((token) => token.pinyin)" class="page-heading">
              <p class="eyebrow">逐字拼音 · {{ showPinyin ? '已显示' : '已隐藏' }}</p>
              <button class="text-button" type="button" @click="showPinyin = !showPinyin">
                {{ showPinyin ? '隐藏拼音' : '显示拼音' }}
              </button>
            </div>
            <ReadingText
              :tokens="currentPage.tokens"
              :show-pinyin="showPinyin"
              :highlight-index="highlightIndex"
              :playable="Boolean(textCanPlay)"
              @play="playPageText"
            />
            <button
              v-if="mode === 'live' && !reviewing && !pageAudioSrc"
              class="button button-ghost button-small page-audio-request"
              type="button"
              :disabled="pageAudioLoading || showAnswerDialog"
              @click="loadPageAudio()"
            >
              {{ pageAudioLoading ? '音频准备中…' : '准备本页朗读音频' }}
            </button>
            <PageAudioPlayer
              ref="pagePlayer"
              :src="pageAudioSrc"
              :disabled="showAnswerDialog"
              :check-expiry="checkChildAuthorization"
              :tokens="currentPage.tokens"
              :mode="mode"
              @media-error="discardPageAudio"
              @before-play="stopAnswerAudio"
              @highlight="highlightIndex = $event"
            />
          </div>
        </article>

        <p v-if="reviewing" class="inline-notice">正在回看已读页，回到当前进度后可继续互动。</p>
        <InteractionCard v-else
          :result="displayedQuestion"
          :followup="answerFollowup"
          :revision="revision"
          :pending="pending || (isParentReading && session.state.syncRequired)"
          :demo="mode === 'demo'"
          :question-ready="questionReady"
          :child-mode="isChildMode"
          :child-answer-options="childAnswerOptions"
          :can-skip="canSkip"
          :answer-audio-url="answerAudioUrl"
          :answer-audio-loading="answerAudioLoading"
          @generate="submitFeedback('INITIAL')"
          @answer="openAnswerDialog()"
          @select-answer="selectChildAnswer"
          @reload-answer-options="refreshChildOptions"
          @not-understood="submitFeedback('NOT_UNDERSTOOD')"
          @understood="submitFeedback('UNDERSTOOD')"
          @skip="submitFeedback('SKIPPED')"
          @play-answer="playAnswerAudio()"
        >
          <p v-if="confirmedAnswerText" class="answer-confirmation">
            <strong>{{ isChildMode ? '已提交回答：' : previousAnswer ? '上一轮回答：' : '家长确认文本：' }}</strong>{{ confirmedAnswerText }}
          </p>
          <div v-if="isParentReading" role="status">
            <p v-if="savedReply" class="answer-confirmation guardian-answer-reply">
              <strong>芽芽：</strong>{{ savedReply }}
            </p>
          </div>
        </InteractionCard>
        <section v-if="mode === 'live' && !reviewing" class="interaction-card">
          <h2>问问芽芽</h2>
          <template v-if="session.state.session.mode === 'CHILD_SOLO'">
            <button v-for="question in ['这一页发生了什么？', '故事里有谁？']" :key="question" type="button" class="button button-soft" :disabled="navigationPending || session.state.syncRequired" @click="askShortQuestion(question)">{{ question }}</button>
          </template>
          <button v-else class="button button-soft" type="button" :disabled="pending || session.state.syncRequired" @click="openAnswerDialog(true)">提出本页问题</button>
          <p v-if="storyAnswer">{{ storyAnswer.outputText }}</p>
          <button v-if="storyAnswer?.interactionEventId && storyAnswer.strategyType !== 'SAFETY_BLOCK'" class="button button-ghost" type="button" :disabled="answerAudioLoading" @click="playAnswerAudio(storyAnswer)">{{ answerAudioLoading ? '回答音频准备中…' : '播放芽芽的回答' }}</button>
        </section>

        <p v-if="answerAudioNotice" class="inline-notice">{{ answerAudioNotice }}</p>
        <p v-if="session.notice.value" class="inline-notice">{{ session.notice.value }}</p>
        <p v-if="session.error.value" class="inline-error">{{ errorMessage }}</p>

        <section v-if="branchChoices.length" class="interaction-card" aria-labelledby="mobile-branch-title">
          <h2 id="mobile-branch-title">选择接下来的故事</h2>
          <button v-for="branch in branchChoices" :key="branch.branchId" class="button button-soft" type="button"
            :disabled="navigationPending || session.state.syncRequired" @click="chooseBranch(branch.branchId)">{{ branch.choiceLabel }}</button>
        </section>
        <nav class="page-navigation" aria-label="翻页">
          <button class="button button-ghost" type="button" :disabled="navigationPending || visitIndex <= 0" @click="goPrevious">上一页</button>
          <button v-if="!branchChoices.length" class="button button-primary" type="button" :disabled="navigationPending" @click="goNext">
            {{ navigationPending ? '同步中…' : (isLastPage ? '完成阅读' : '下一页 →') }}
          </button>
        </nav>
      </template>
    </main>

    <main v-else class="reader-empty">
      <template v-if="session.loading.value">
        <span class="loading-orbit" />
        <p>正在建立阅读会话…</p>
      </template>
      <section v-else class="reader-error-card">
        <span class="error-mark">!</span>
        <h1>{{ mode === 'live' ? '无法建立实机会话' : '无法建立演示会话' }}</h1>
        <p>{{ errorMessage || '阅读会话尚未建立' }}</p>
        <p v-if="serverErrorHint" class="error-hint">{{ serverErrorHint }}</p>
        <code v-if="session.error.value">
          <template v-if="session.error.value.status">HTTP {{ session.error.value.status }}</template>
          <template v-else>{{ session.error.value.code || 'REQUEST_FAILED' }}</template>
          <template v-if="session.error.value.requestId"> · {{ session.error.value.requestId }}</template>
        </code>
        <div class="reader-error-actions">
          <p v-if="creationUnconfirmed">请返回成长记录核对刚创建的会话，可从记录继续阅读。</p>
          <button v-else class="button button-primary" type="button" @click="retryInitialize">重试</button>
          <button class="button button-ghost" type="button" @click="exitReading">返回</button>
        </div>
      </section>
    </main>

    <VoiceAnswerDialog
      v-if="showAnswerDialog && session.state.session"
      :key="`${currentPageNumber}-${revision}`"
      :voice-api="voiceApi"
      :session-id="sessionId"
      :page-number="currentPageNumber"
      :revision="revision"
      :demo="mode === 'demo'"
      :submitting="pending"
      :initial-text="answerDraft"
      :submit-error="errorMessage"
      :asking="asking"
      @close="showAnswerDialog = false; answerDraft = ''"
      @confirmed="handleAnswerConfirmed"
    />
  </div>
</template>




