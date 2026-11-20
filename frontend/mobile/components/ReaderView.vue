<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import InteractionCard from './InteractionCard.vue'
import PageAudioPlayer from './PageAudioPlayer.vue'
import ReadingText from './ReadingText.vue'
import ReadingSummary from './ReadingSummary.vue'
import VoiceAnswerDialog from './VoiceAnswerDialog.vue'
import { useAudioController } from '../composables/useAudioController'
import { useReadingSession } from '../composables/useReadingSession'

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
const emit = defineEmits(['exit', 'finished'])

const session = useReadingSession({
  api: props.sessionApi,
  initialSessionId: props.initialSessionId,
  initialSnapshot: props.initialSnapshot,
  childProfileId: props.childProfileId,
  storyVersionId: props.storyVersionId,
})
const currentPageNumber = ref(props.book.pages[0]?.page ?? 1)
const showPinyin = ref(true)
const highlightIndex = ref(-1)
const interactionResult = ref(null)
const questionReady = ref(false)
const showAnswerDialog = ref(false)
const lastAnswerText = ref('')
const answerDraft = ref('')
const pageAudioSrc = ref('')
const pageAudioLoading = ref(false)
const answerAudioUrl = ref('')
const answerAudioLoading = ref(false)
const answerAudioNotice = ref('')
const localPending = ref(false)
const answerAudio = useAudioController()
let requestGeneration = 0
let pageRequestGeneration = 0
let active = true

const currentPageIndex = computed(() => {
  const index = props.book.pages.findIndex((page) => page.page === currentPageNumber.value)
  return index
})
const currentPage = computed(() => props.book.pages[currentPageIndex.value])
const progressText = computed(() => `${currentPageIndex.value + 1} / ${props.book.pages.length}`)
const progressPercent = computed(() => ((currentPageIndex.value + 1) / props.book.pages.length) * 100)
const visited = computed(() => [...new Set(session.state.session?.visitedPageNumbers ?? [])])
const reviewing = computed(() => currentPageNumber.value !== session.state.session?.currentPage)
const nextPageNumber = computed(() => {
  if (reviewing.value) return visited.value[visited.value.indexOf(currentPageNumber.value) + 1]
  if (props.mode === 'live') return currentPage.value?.defaultNextPageNumber
  return props.book.pages[currentPageIndex.value + 1]?.page
})
const isLastPage = computed(() => !reviewing.value && nextPageNumber.value == null)
const isFinished = computed(() => session.state.session?.status === 'FINISHED')
const pending = computed(() => session.state.pending || localPending.value || session.loading.value)
const revision = computed(() => session.state.session?.revision ?? 0)
const sessionId = computed(() => session.state.session?.readingSessionId ?? null)
const errorMessage = computed(() => session.error.value?.message ?? session.error.value ?? '')
const creationUnconfirmed = computed(() => !props.initialSessionId && !session.state.session && session.error.value?.code === 'REQUEST_OUTCOME_UNKNOWN')
const serverErrorHint = computed(() => (session.error.value?.status ?? 0) >= 500 ? '请确认 127.0.0.1:8000 后端已启动、账号已登录且 Cookie 有效；若后端正在运行，请查看服务端日志。' : '')

watch(currentPageNumber, () => {
  resetInteractionForPage()
  restorePageQuestion()
})
watch(() => session.state.events, () => restorePageQuestion())
watch(() => session.state.session?.currentPage, (page) => {
  if (page != null) currentPageNumber.value = page
})
onBeforeUnmount(() => {
  active = false
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
    currentPageNumber.value = session.state.session.currentPage
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

function resetInteractionForPage() {
  requestGeneration += 1
  pageRequestGeneration += 1
  answerAudio.stop()
  answerAudioUrl.value = ''
  answerAudioNotice.value = ''
  interactionResult.value = null
  questionReady.value = false
  lastAnswerText.value = ''
  answerDraft.value = ''
  showAnswerDialog.value = false
  highlightIndex.value = -1
  pageAudioSrc.value = props.mode === 'demo' ? (currentPage.value?.audio ?? '') : ''
}

function restorePageQuestion() {
  if (reviewing.value || session.state.session?.status !== 'IN_PROGRESS') return
  const question = session.state.events.findLast((event) =>
    event.pageNumber === currentPageNumber.value && event.outputText?.trim() &&
    !['SKIP', 'SAFETY_BLOCK'].includes(event.strategyType) &&
    (event.eventType === 'QUESTION' ||
      (event.eventType === 'FEEDBACK' && ['UNDERSTOOD', 'NOT_UNDERSTOOD'].includes(event.feedback))))
  if (question) {
    interactionResult.value = question
    questionReady.value = true
  }
}

async function syncReading() {
  try {
    const recovered = await session.restore()
    if (!active) return
    currentPageNumber.value = session.state.session.currentPage
    if (recovered?.result) {
      interactionResult.value = recovered.result
      questionReady.value = true
    }
    if (recovered?.confirmedAnswer) {
      lastAnswerText.value = recovered.confirmedAnswer
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
  if (pending.value || !session.state.session) return
  answerAudio.stop()
  if (isLastPage.value) {
    await finishReading()
    return
  }
  const next = nextPageNumber.value
  if (!props.book.pages.some((page) => page.page === next)) return
  if (reviewing.value) {
    currentPageNumber.value = next
    return
  }
  try {
    await session.changePage(next)
    currentPageNumber.value = session.state.session.currentPage
  } catch {
    // Conflict recovery is reported through `session.notice`.
  }
}

function goPrevious() {
  const index = visited.value.indexOf(currentPageNumber.value)
  if (pending.value || index <= 0) return
  answerAudio.stop()
  currentPageNumber.value = visited.value[index - 1]
}

async function finishReading() {
  if (pending.value) return
  answerAudio.stop()
  try {
    await session.finish()
    emit('finished', { book: props.book, session: session.state.session, report: session.state.report })
  } catch {
    // Error is rendered from the composable state.
  }
}

async function generateQuestion() {
  if (pending.value || !session.state.session) return
  localPending.value = true
  const page = currentPageNumber.value
  const generation = ++requestGeneration
  try {
    const result = await session.guidance('INITIAL')
    if (generation !== requestGeneration || page !== currentPageNumber.value) return
    interactionResult.value = result
    questionReady.value = true
  } catch {
    // Error is rendered from the composable state.
  } finally {
    localPending.value = false
  }
}

async function submitFeedback(feedback) {
  if (pending.value || !session.state.session) return
  localPending.value = true
  const page = currentPageNumber.value
  const generation = ++requestGeneration
  try {
    const result = await session.guidance(feedback)
    if (generation !== requestGeneration || page !== currentPageNumber.value) return
    interactionResult.value = result
    questionReady.value = true
  } catch (error) {
    if (error?.code === 'PROMPT_REQUIRED') questionReady.value = false
  } finally {
    localPending.value = false
  }
}

async function openAnswerDialog() {
  if (!questionReady.value) await generateQuestion()
  if (questionReady.value) showAnswerDialog.value = true
}

async function handleAnswerConfirmed({ text }) {
  if (pending.value || reviewing.value || session.state.syncRequired) return
  answerDraft.value = text
  try {
    await session.answer(text)
    lastAnswerText.value = text
    answerDraft.value = ''
    showAnswerDialog.value = false
  } catch {
    // Keep the edited text for a rejected write. Uncertain writes use session reconciliation.
    if (session.state.syncRequired || session.notice.value) showAnswerDialog.value = false
  }
}

async function loadPageAudio() {
  if (!session.state.session || pageAudioLoading.value) return
  const page = currentPageNumber.value
  const generation = pageRequestGeneration
  pageAudioLoading.value = true
  try {
    const response = await props.voiceApi.synthesizePage({
      readingSessionId: session.state.session.readingSessionId,
      pageNumber: page,
      revision: revision.value,
    })
    if (generation !== pageRequestGeneration || page !== currentPageNumber.value) return
    pageAudioSrc.value = response.data.audioUrl
  } catch (error) {
    if (generation === pageRequestGeneration && page === currentPageNumber.value) {
      answerAudioNotice.value = error?.message ?? '页面朗读音频准备失败'
    }
  } finally {
    pageAudioLoading.value = false
  }
}

async function playAnswerAudio() {
  answerAudioNotice.value = ''
  if (props.mode === 'demo') {
    answerAudioNotice.value = '演示模式没有回答音频；实机模式将在 G10 返回 interactionEventId 后请求 API-023。'
    return
  }
  const eventId = interactionResult.value?.interactionEventId
  if (!eventId || answerAudioLoading.value || interactionResult.value.revision !== revision.value) {
    answerAudioNotice.value = '当前结果没有可用的 interactionEventId，无法请求回答音频。'
    return
  }
  const page = currentPageNumber.value
  const generation = pageRequestGeneration
  answerAudioLoading.value = true
  try {
    if (!answerAudioUrl.value) {
      const response = await props.voiceApi.synthesizeInteraction({ interactionEventId: eventId })
      if (generation !== pageRequestGeneration || page !== currentPageNumber.value || eventId !== interactionResult.value?.interactionEventId || interactionResult.value.revision !== revision.value) return
      answerAudioUrl.value = response.data.audioUrl
    }
    await answerAudio.play({ url: answerAudioUrl.value, source: 'answer' })
  } catch (error) {
    if (generation === pageRequestGeneration && page === currentPageNumber.value) {
      answerAudioNotice.value = error?.message ?? '回答音频准备失败'
    }
  } finally {
    answerAudioLoading.value = false
  }
}

watch(() => interactionResult.value?.interactionEventId, () => {
  answerAudio.stop()
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
      <section v-if="session.state.syncRequired" class="reader-error-card">
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
        :book="book"
        :session="session.state.session"
        :report="session.state.report"
        @exit="exitReading"
      />

      <p v-else-if="session.state.session.status !== 'IN_PROGRESS' || !currentPage || session.state.session.storyVersionId !== book.storyVersionId">本次会话或对应书页不可用，请返回绘本馆。</p>
      <template v-else>
        <article class="page-card">
          <div class="page-visual">
            <img :src="currentPage.image || book.cover" :alt="`${book.title} 第 ${currentPageIndex + 1} 页`" />
            <span class="page-scene">{{ currentPage.scene || `第 ${currentPageIndex + 1} 幕` }}</span>
          </div>
          <div class="page-reading">
            <div v-if="currentPage.tokens.some((token) => token.pinyin)" class="page-heading">
              <p class="eyebrow">逐字拼音 · {{ showPinyin ? '已显示' : '已隐藏' }}</p>
              <button class="text-button" type="button" @click="showPinyin = !showPinyin">
                {{ showPinyin ? '隐藏拼音' : '显示拼音' }}
              </button>
            </div>
            <ReadingText :tokens="currentPage.tokens" :show-pinyin="showPinyin" :highlight-index="highlightIndex" />
            <button
              v-if="mode === 'live' && !reviewing && !pageAudioSrc"
              class="button button-ghost button-small page-audio-request"
              type="button"
              :disabled="pageAudioLoading"
              @click="loadPageAudio"
            >
              {{ pageAudioLoading ? '音频准备中…' : '准备本页朗读音频' }}
            </button>
            <PageAudioPlayer
              :src="pageAudioSrc"
              :tokens="currentPage.tokens"
              :mode="mode"
              @highlight="highlightIndex = $event"
            />
          </div>
        </article>

        <p v-if="reviewing" class="inline-notice">正在回看已读页，回到当前进度后可继续互动。</p>
        <InteractionCard v-else
          :result="interactionResult"
          :revision="revision"
          :pending="pending"
          :demo="mode === 'demo'"
          :question-ready="questionReady"
          :answer-audio-url="answerAudioUrl"
          :answer-audio-loading="answerAudioLoading"
          @generate="generateQuestion"
          @answer="openAnswerDialog"
          @not-understood="submitFeedback('NOT_UNDERSTOOD')"
          @understood="submitFeedback('UNDERSTOOD')"
          @skip="submitFeedback('SKIPPED')"
          @play-answer="playAnswerAudio"
        />

        <p v-if="lastAnswerText" class="answer-confirmation">
          <strong>家长确认文本：</strong>{{ lastAnswerText }}
        </p>
        <p v-if="answerAudioNotice" class="inline-notice">{{ answerAudioNotice }}</p>
        <p v-if="session.notice.value" class="inline-notice">{{ session.notice.value }}</p>
        <p v-if="session.error.value" class="inline-error">{{ errorMessage }}</p>

        <nav class="page-navigation" aria-label="翻页">
          <button class="button button-ghost" type="button" :disabled="pending || visited.indexOf(currentPageNumber) <= 0" @click="goPrevious">上一页</button>
          <button class="button button-primary" type="button" :disabled="pending" @click="goNext">
            {{ pending ? '同步中…' : (isLastPage ? '完成阅读' : '下一页 →') }}
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
      @close="showAnswerDialog = false; answerDraft = ''"
      @confirmed="handleAnswerConfirmed"
    />
  </div>
</template>




