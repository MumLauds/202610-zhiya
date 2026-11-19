<script setup>
import { computed, onMounted, ref, watch } from 'vue'
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
})
const emit = defineEmits(['exit', 'finished'])

const session = useReadingSession({
  api: props.sessionApi,
  initialSessionId: props.initialSessionId,
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
const pageAudioSrc = ref('')
const pageAudioLoading = ref(false)
const answerAudioUrl = ref('')
const answerAudioLoading = ref(false)
const answerAudioNotice = ref('')
const localPending = ref(false)
const answerAudio = useAudioController()
let requestGeneration = 0
let pageRequestGeneration = 0

const currentPageIndex = computed(() => {
  const index = props.book.pages.findIndex((page) => page.page === currentPageNumber.value)
  return index >= 0 ? index : 0
})
const currentPage = computed(() => props.book.pages[currentPageIndex.value])
const progressText = computed(() => `${currentPageIndex.value + 1} / ${props.book.pages.length}`)
const progressPercent = computed(() => ((currentPageIndex.value + 1) / props.book.pages.length) * 100)
const isLastPage = computed(() => currentPageIndex.value >= props.book.pages.length - 1)
const isFinished = computed(() => session.state.session?.status === 'FINISHED')
const pending = computed(() => session.state.pending || localPending.value || session.loading.value)
const revision = computed(() => session.state.session?.revision ?? 0)
const sessionId = computed(() => session.state.session?.readingSessionId ?? null)
const errorMessage = computed(() => session.error.value?.message ?? session.error.value ?? '')
const serverErrorHint = computed(() => (session.error.value?.status ?? 0) >= 500 ? '请确认 127.0.0.1:8000 后端已启动、账号已登录且 Cookie 有效；若后端正在运行，请查看服务端日志。' : '')

watch(currentPageNumber, () => resetInteractionForPage())

onMounted(async () => {
  await initializeReader()
})

async function initializeReader() {
  try {
    await session.initialize()
    currentPageNumber.value = session.state.session?.currentPage ?? currentPage.value.page
    if (session.state.session?.status === 'CREATED') await session.start()
    if (props.mode === 'demo') pageAudioSrc.value = currentPage.value.audio
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
  showAnswerDialog.value = false
  highlightIndex.value = -1
  pageAudioSrc.value = props.mode === 'demo' ? (currentPage.value?.audio ?? '') : ''
}

async function goNext() {
  if (pending.value || !session.state.session) return
  answerAudio.stop()
  if (isLastPage.value) {
    await finishReading()
    return
  }
  const nextPage = props.book.pages[currentPageIndex.value + 1]
  const serverPage = session.state.session?.currentPage ?? currentPageNumber.value
  if (nextPage.page <= serverPage) {
    currentPageNumber.value = nextPage.page
    return
  }
  try {
    await session.changePage(nextPage.page)
    currentPageNumber.value = nextPage.page
  } catch {
    // Conflict recovery is reported through `session.notice`.
  }
}

function goPrevious() {
  if (pending.value || currentPageIndex.value <= 0) return
  answerAudio.stop()
  currentPageNumber.value = props.book.pages[currentPageIndex.value - 1].page
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
    if (error?.code === 'PROMPT_REQUIRED') await generateQuestion()
  } finally {
    localPending.value = false
  }
}

async function openAnswerDialog() {
  if (!questionReady.value) await generateQuestion()
  if (questionReady.value) showAnswerDialog.value = true
}

async function handleAnswerConfirmed({ text }) {
  showAnswerDialog.value = false
  lastAnswerText.value = text
  await submitFeedback('UNDERSTOOD')
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
  if (!interactionResult.value?.interactionEventId) {
    answerAudioNotice.value = '当前结果没有可用的 interactionEventId，无法请求回答音频。'
    return
  }
  const page = currentPageNumber.value
  const generation = pageRequestGeneration
  answerAudioLoading.value = true
  try {
    const response = await props.voiceApi.synthesizeInteraction({
      interactionEventId: interactionResult.value.interactionEventId,
    })
    if (generation !== pageRequestGeneration || page !== currentPageNumber.value) return
    answerAudioUrl.value = response.data.audioUrl
    await answerAudio.play({ url: answerAudioUrl.value, source: 'answer' })
  } catch (error) {
    if (generation === pageRequestGeneration && page === currentPageNumber.value) {
      answerAudioNotice.value = error?.message ?? '回答音频准备失败'
    }
  } finally {
    answerAudioLoading.value = false
  }
}

async function exitReading() {
  if (session.state.session?.status === 'IN_PROGRESS') {
    try {
      await session.finish()
      emit('finished', { book: props.book, session: session.state.session, report: session.state.report })
    } catch {
      // Leave the reader even if finishing the session fails.
    }
  }
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
      <ReadingSummary
        v-if="isFinished"
        :book="book"
        :session="session.state.session"
        :report="session.state.report"
        @exit="exitReading"
      />

      <template v-else>
        <article class="page-card">
          <div class="page-visual">
            <img :src="currentPage.image || book.cover" :alt="`${book.title} 第 ${currentPageIndex + 1} 页`" />
            <span class="page-scene">{{ currentPage.scene || `第 ${currentPageIndex + 1} 幕` }}</span>
          </div>
          <div class="page-reading">
            <div class="page-heading">
              <p class="eyebrow">逐字拼音 · {{ showPinyin ? '已显示' : '已隐藏' }}</p>
              <button class="text-button" type="button" @click="showPinyin = !showPinyin">
                {{ showPinyin ? '隐藏拼音' : '显示拼音' }}
              </button>
            </div>
            <ReadingText :tokens="currentPage.tokens" :show-pinyin="showPinyin" :highlight-index="highlightIndex" />
            <button
              v-if="mode === 'live' && !pageAudioSrc"
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

        <InteractionCard
          :result="interactionResult"
          :pending="pending"
          :demo="mode === 'demo'"
          :question-ready="questionReady"
          :answer-audio-url="answerAudioUrl"
          :answer-audio-loading="answerAudioLoading"
          @generate="generateQuestion"
          @answer="openAnswerDialog"
          @not-understood="submitFeedback('NOT_UNDERSTOOD')"
          @skip="submitFeedback('SKIPPED')"
          @play-answer="playAnswerAudio"
        />

        <p v-if="lastAnswerText" class="answer-confirmation">
          <strong>家长确认文本：</strong>{{ lastAnswerText }}
          <span>当前接口契约没有答案文本持久化字段，因此只提交本轮 UNDERSTOOD 反馈。</span>
        </p>
        <p v-if="answerAudioNotice" class="inline-notice">{{ answerAudioNotice }}</p>
        <p v-if="session.notice.value" class="inline-notice">{{ session.notice.value }}</p>
        <p v-if="session.error.value" class="inline-error">{{ errorMessage }}</p>

        <nav class="page-navigation" aria-label="翻页">
          <button class="button button-ghost" type="button" :disabled="pending || currentPageIndex <= 0" @click="goPrevious">上一页</button>
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
          <button class="button button-primary" type="button" @click="retryInitialize">重试</button>
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
      @close="showAnswerDialog = false"
      @confirmed="handleAnswerConfirmed"
    />
  </div>
</template>




