<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import FlipBook from '../components/FlipBook.vue'
import { synthesize, transcribe } from '../api/voice'
import { describeApiError } from '../api/client'

const props = defineProps({
  book: { type: Object, default: null },
  session: { type: Object, default: null },
  report: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  authenticated: { type: Boolean, default: false },
  syncRequired: { type: Boolean, default: false },
  recordPage: { type: Function, required: true },
  completeReading: { type: Function, required: true },
  refreshReading: { type: Function, required: true },
  availableBooks: { type: Array, default: () => [] },
  enterChildModeAction: { type: Function, default: async () => false },
})

const emit = defineEmits(['back', 'library', 'start-session'])
const flipBook = ref(null)
const pageIndex = ref(0)
const turnBusy = ref(false)
const audioStatus = ref('idle')
const audioMessage = ref('')
const childModeOpen = ref(false)
const childModeBusy = ref(false)
const childMinutes = ref(10)
const childAllowedVersions = ref([])
const voiceOpen = ref(false)
const voiceState = ref('idle')
const voiceError = ref('')
const transcript = ref(null)
const transcriptEditor = ref(null)
let recorder = null
let recordingStream = null
let submitRecording = false
let voiceGeneration = 0
let recognitionRequest = null
let audio = null
let audioGeneration = 0
let speechRequest = null

const reviewingHistory = computed(() => ['FINISHED', 'INTERRUPTED'].includes(props.session?.status) && props.session.visitedPageNumbers.length > 0)
const readerPages = computed(() => {
  const pages = props.book?.pages || []
  if (!reviewingHistory.value) return pages
  const byNumber = new Map(pages.map(page => [page.pageNumber, page]))
  return props.session.visitedPageNumbers.map(number => byNumber.get(number)).filter(Boolean)
})
const currentPage = computed(() => readerPages.value[pageIndex.value] || readerPages.value[0] || null)
const isLast = computed(() => currentPage.value?.defaultNextPageNumber == null)
const nextIndex = computed(() => reviewingHistory.value
  ? pageIndex.value + 1
  : readerPages.value.findIndex(page => page.pageNumber === currentPage.value?.defaultNextPageNumber))
const rereading = computed(() => currentPage.value?.pageNumber !== props.session?.currentPage)
const canFinish = computed(() => !props.loading && !turnBusy.value && !props.syncRequired && props.session?.status === 'IN_PROGRESS' && (props.session?.preview || props.authenticated))
const mediaAllowed = computed(() => canFinish.value && !rereading.value)
const childBookChoices = computed(() => {
  const items = props.book ? [props.book, ...props.availableBooks] : props.availableBooks
  return [...new Map(items.filter((item) => item.storyVersionId).map((item) => [item.storyVersionId, item])).values()]
})

watch(transcriptEditor, (editor) => editor?.focus(), { flush: 'post' })

function showIndex(index) {
  pageIndex.value = Math.max(0, index)
  flipBook.value?.showPage(pageIndex.value)
}

function showPage(pageNumber) {
  showIndex(readerPages.value.findLastIndex(page => page.pageNumber === pageNumber))
}

watch(
  [() => props.session?.readingSessionId, () => props.session?.currentPage, () => props.book, reviewingHistory],
  ([id, current, book, reviewing], [previousId, , , wasReviewing]) => {
    const justFinished = reviewing && !wasReviewing && id === previousId
    showPage(justFinished ? book?.pages[pageIndex.value]?.pageNumber : current)
  },
  { immediate: true },
)

async function onPageChange({ storyIndex }) {
  const target = readerPages.value[storyIndex]
  if (!target || turnBusy.value || props.syncRequired) {
    flipBook.value?.showPage(pageIndex.value)
    return
  }
  if (storyIndex === pageIndex.value) return
  stopNarration()
  closeVoice()
  if (reviewingHistory.value || props.session.visitedPageNumbers.includes(target.pageNumber)) {
    showIndex(storyIndex)
    return
  }
  const serverPage = props.book.pages.find((page) => page.pageNumber === props.session.currentPage)
  if (target.pageNumber !== serverPage?.defaultNextPageNumber || props.session.status !== 'IN_PROGRESS') {
    flipBook.value?.showPage(pageIndex.value)
    return
  }
  // Keep the widget and text on confirmed progress while the write is pending.
  flipBook.value?.showPage(pageIndex.value)
  turnBusy.value = true
  try {
    await props.recordPage(target.pageNumber)
  } catch (error) {
    audioMessage.value = describeApiError(error)
  } finally {
    showPage(props.session.currentPage)
    turnBusy.value = false
  }
}

function goPrevious() {
  if (reviewingHistory.value) return onPageChange({ storyIndex: pageIndex.value - 1 })
  const visited = props.session.visitedPageNumbers
  const index = visited.indexOf(currentPage.value?.pageNumber)
  if (index > 0) onPageChange({ storyIndex: props.book.pages.findIndex((page) => page.pageNumber === visited[index - 1]) })
}

function goNext() {
  return onPageChange({ storyIndex: nextIndex.value })
}

async function finishReading() {
  if (!canFinish.value) return
  stopNarration()
  closeVoice()
  turnBusy.value = true
  try { await props.completeReading() } catch (error) { audioMessage.value = describeApiError(error) }
  finally { turnBusy.value = false }
}

function stopNarration() {
  audioGeneration += 1
  speechRequest?.abort()
  speechRequest = null
  if (audio) {
    audio.onended = audio.onerror = null
    audio.pause()
    audio.removeAttribute('src')
    audio.load()
    audio = null
  }
  window.speechSynthesis?.cancel()
  audioStatus.value = 'idle'
  audioMessage.value = ''
}

function voiceSnapshot() {
  return { readingSessionId: props.session.readingSessionId, pageNumber: currentPage.value.pageNumber, revision: props.session.revision }
}

async function playNarration() {
  if (['playing', 'loading'].includes(audioStatus.value)) {
    stopNarration()
    return
  }
  if (!currentPage.value || !mediaAllowed.value) return
  stopNarration()
  const generation = audioGeneration
  const pageNumber = currentPage.value.pageNumber
  if (!props.session.preview) {
    try {
      speechRequest = new AbortController()
      audioStatus.value = 'loading'
      audioMessage.value = '正在生成朗读'
      const data = await synthesize(voiceSnapshot(), speechRequest.signal)
      if (generation !== audioGeneration) return
      audio = new Audio(data.audioUrl)
      audio.onended = () => { stopNarration(); audioMessage.value = '朗读完成' }
      audio.onerror = () => { stopNarration(); audioMessage.value = '播放失败，文字仍可阅读' }
      await audio.play()
      if (generation !== audioGeneration) return
      audioStatus.value = 'playing'
      audioMessage.value = `正在朗读第 ${pageNumber} 页`
    } catch (error) {
      if (generation !== audioGeneration) return
      stopNarration()
      audioStatus.value = 'error'
      audioMessage.value = describeApiError(error)
    }
    return
  }
  if ('speechSynthesis' in window) {
    speechSynthesis.cancel()
    const utterance = new SpeechSynthesisUtterance(currentPage.value.text)
    utterance.lang = 'zh-CN'
    utterance.rate = .92
    utterance.onend = () => { if (generation === audioGeneration) { audioStatus.value = 'idle'; audioMessage.value = '朗读完成' } }
    utterance.onerror = () => { if (generation === audioGeneration) { audioStatus.value = 'error'; audioMessage.value = '浏览器朗读失败，文字仍可阅读' } }
    audioStatus.value = 'playing'
    audioMessage.value = '正在使用浏览器朗读'
    speechSynthesis.speak(utterance)
  } else {
    audioStatus.value = 'error'
    audioMessage.value = '当前浏览器不支持语音播放'
  }
}

async function toggleRecording() {
  voiceError.value = ''
  if (['starting', 'processing'].includes(voiceState.value)) return
  if (voiceState.value === 'recording') {
    submitRecording = true
    voiceState.value = 'processing'
    recorder?.stop()
    return
  }
  if (!mediaAllowed.value) return
  if (props.session?.preview || !props.authenticated) {
    voiceState.value = 'unavailable'
    voiceError.value = '当前是本地预览模式。登录并创建后端阅读会话后才能上传录音。'
    editQuestionText()
    return
  }
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    voiceState.value = 'unavailable'
    voiceError.value = '当前浏览器不支持录音，可以输入文字。'
    editQuestionText()
    return
  }
  const generation = ++voiceGeneration
  submitRecording = false
  const snapshot = voiceSnapshot()
  let stream = null
  try {
    voiceState.value = 'starting'
    stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    if (generation !== voiceGeneration) {
      stream.getTracks().forEach((track) => track.stop())
      return
    }
    recordingStream = stream
    const chunks = []
    const activeRecorder = new MediaRecorder(stream)
    recorder = activeRecorder
    activeRecorder.ondataavailable = (event) => {
      if (event.data.size) chunks.push(event.data)
    }
    activeRecorder.onstop = async () => {
      stream.getTracks().forEach((track) => track.stop())
      if (generation !== voiceGeneration) return
      recorder = recordingStream = null
      if (!submitRecording) { voiceState.value = 'idle'; return }
      if (!chunks.some((chunk) => chunk.size)) {
        voiceState.value = 'error'
        voiceError.value = '没有录到声音，请重新录制。'
        return
      }
      const blob = new Blob(chunks, { type: activeRecorder.mimeType || 'audio/webm' })
      voiceState.value = 'processing'
      try {
        recognitionRequest = new AbortController()
        const result = await transcribe(blob, snapshot, recognitionRequest.signal)
        if (generation !== voiceGeneration) return
        transcript.value = { text: result.text, status: result.reliable === true ? 'recognized' : 'draft', source: 'recording' }
        voiceState.value = 'idle'
      } catch (error) {
        if (generation !== voiceGeneration) return
        voiceState.value = 'error'
        voiceError.value = describeApiError(error)
      }
    }
    activeRecorder.onerror = () => {
      if (generation !== voiceGeneration) return
      cancelRecording()
      voiceState.value = 'error'
      voiceError.value = '录音失败，请重新录制。'
    }
    activeRecorder.start()
    transcript.value = null
    voiceState.value = 'recording'
  } catch {
    stream?.getTracks().forEach((track) => track.stop())
    if (generation !== voiceGeneration) return
    cancelRecording()
    voiceState.value = 'error'
    voiceError.value = '麦克风不可用，可以输入文字。'
    editQuestionText()
  }
}

function editQuestionText() {
  if (transcript.value?.status === 'draft') return
  transcript.value = { text: transcript.value?.text || '', status: 'draft', source: 'typed' }
}

function confirmTranscript() {
  const draft = transcript.value
  if (draft?.status !== 'draft' || !draft.text.trim()) return
  draft.text = draft.text.trim()
  draft.status = 'confirmed'
  voiceError.value = ''
}

function openVoice(typed = false) {
  if (!mediaAllowed.value) return
  stopNarration()
  cancelRecording()
  voiceOpen.value = true
  voiceState.value = 'idle'
  transcript.value = null
  voiceError.value = ''
  if (typed) editQuestionText()
}

function cancelRecording() {
  voiceGeneration += 1
  submitRecording = false
  recognitionRequest?.abort()
  recognitionRequest = null
  if (recorder) {
    recorder.onstop = recorder.ondataavailable = recorder.onerror = null
    if (recorder.state !== 'inactive') recorder.stop()
    recorder = null
  }
  recordingStream?.getTracks().forEach((track) => track.stop())
  recordingStream = null
  voiceState.value = 'idle'
}

function closeVoice() {
  cancelRecording()
  transcript.value = null
  voiceOpen.value = false
}

function leaveReader() {
  stopNarration()
  closeVoice()
  emit('back', props.book)
}

function openChildMode() {
  if (!props.authenticated || props.session?.preview || props.session?.status !== 'CREATED' || props.session.mode !== 'PARENT_CHILD') return
  childMinutes.value = 10
  childAllowedVersions.value = props.book?.storyVersionId ? [props.book.storyVersionId] : []
  childModeOpen.value = true
}

function toggleChildBook(versionId) {
  if (versionId === props.book?.storyVersionId) return
  childAllowedVersions.value = childAllowedVersions.value.includes(versionId)
    ? childAllowedVersions.value.filter((id) => id !== versionId)
    : [...childAllowedVersions.value, versionId]
}

async function submitChildMode() {
  if (childModeBusy.value || !childAllowedVersions.value.length) return
  childModeBusy.value = true
  try {
    const entered = await props.enterChildModeAction(
      props.session.readingSessionId,
      childAllowedVersions.value,
      childMinutes.value * 60,
    )
    if (entered) childModeOpen.value = false
  } finally {
    childModeBusy.value = false
  }
}

async function refreshProgress() {
  if (turnBusy.value) return
  const syncingProgress = props.syncRequired
  turnBusy.value = true
  try { await props.refreshReading() } catch (error) { audioMessage.value = describeApiError(error) }
  finally {
    if (syncingProgress) showPage(props.session?.currentPage)
    turnBusy.value = false
  }
}

watch(() => [props.session?.readingSessionId, props.session?.currentPage, props.session?.revision, props.session?.status, props.authenticated, props.loading, props.syncRequired], () => {
  stopNarration()
  closeVoice()
})
onBeforeUnmount(() => { stopNarration(); closeVoice() })
</script>

<template>
  <main v-if="loading" class="reader-page page-width">
    <div class="reader-loading"><div><div class="spinner" style="margin:0 auto 12px" /><p>正在恢复阅读会话…</p></div></div>
  </main>
  <main v-else-if="book && session" class="reader-page page-width">
    <div class="reader-heading">
      <div>
        <button class="text-button" type="button" @click="leaveReader">← 返回绘本详情</button>
        <h1>{{ book.title }}</h1>
        <p>第 {{ currentPage?.pageNumber }} 页 · 共 {{ book.pageCount }} 页 · {{ session.preview ? '本地预览' : syncRequired ? '进度待确认' : turnBusy ? '正在同步进度' : rereading ? `回看已读页，服务端进度为第 ${session.currentPage} 页` : '后端会话已同步' }}</p>
        <p v-if="rereading && session.status === 'IN_PROGRESS'">回看期间仅阅读文字和插图；返回当前进度后可继续朗读与录音。</p>
        <button v-if="syncRequired" class="secondary" type="button" :disabled="turnBusy" @click="refreshProgress">确认并同步进度</button>
        <button v-else-if="session.status === 'CREATED'" class="primary" type="button" @click="emit('start-session')">开始本次阅读</button>
        <button v-if="!isLast && session.status === 'IN_PROGRESS'" class="secondary" type="button" :disabled="!canFinish" @click="finishReading">结束本次阅读</button>
      </div>
      <span class="pill">{{ report ? '本次阅读已完成' : '亲子共读模式' }}</span>
    </div>

    <div class="reader-layout">
      <section class="reader-main">
        <div class="flipbook-stage">
          <FlipBook
            ref="flipBook"
            :key="`${session.readingSessionId}-${reviewingHistory}`"
            :pages="readerPages"
            :page-count="book.pageCount"
            :reviewing-history="reviewingHistory"
            :title="book.title"
            :start-page="pageIndex"
            :disabled="turnBusy || syncRequired"
            @page-change="onPageChange"
            @error="audioMessage = $event"
          />
          <p class="flip-hint">拖动书页边缘，或点击外侧翻页</p>
        </div>
        <nav class="reader-dock" aria-label="阅读控制">
          <button class="dock-button" type="button" :disabled="pageIndex === 0 || turnBusy || syncRequired" @click="goPrevious"><span class="dock-glyph">‹</span>上一页</button>
          <button class="dock-button" type="button" :disabled="!mediaAllowed" @click="playNarration"><span class="dock-glyph">{{ audioStatus === 'playing' ? 'Ⅱ' : '▶' }}</span>{{ audioStatus === 'playing' ? '暂停朗读' : audioStatus === 'loading' ? '取消朗读' : '朗读' }}</button>
          <button class="dock-button primary" type="button" :disabled="!mediaAllowed" @click="openVoice()"><span class="dock-glyph">芽</span>语音识别</button>
          <button v-if="isLast && !readerPages[nextIndex]" class="dock-button" type="button" :disabled="!canFinish" @click="finishReading"><span class="dock-glyph">✓</span>完成</button>
          <button v-else class="dock-button" type="button" :disabled="turnBusy || syncRequired || !readerPages[nextIndex] || (!reviewingHistory && session.status !== 'IN_PROGRESS')" @click="goNext"><span class="dock-glyph">›</span>下一页</button>
        </nav>
      </section>

      <aside class="reader-side">
        <div v-if="audioMessage" class="side-card audio">
          <div class="audio-line"><span>{{ audioMessage }}</span></div>
        </div>
        <section class="side-card question-card">
          <h2>✦ 共读小提问</h2>
          <p>{{ session.preview ? `本地预览问题：${currentPage?.question}` : '互动问答尚未接入，暂不能提交理解程度或生成追问。' }}</p>
          <div class="question-actions">
            <button class="secondary" type="button" disabled>已理解（暂不可用）</button>
            <button class="secondary" type="button" disabled>还没懂（暂不可用）</button>
            <button class="primary" type="button" :disabled="!mediaAllowed" @click="openVoice(true)">输入文字</button>
          </div>
        </section>
        <section class="side-card parent-tip">
          <h2>给家长的共读提示</h2>
          <p>{{ session.preview ? currentPage?.answer : '可以和孩子一起阅读当前页。个性化共读提示尚未接入。' }}</p>
        </section>
        <section v-if="authenticated && !session.preview" class="side-card child-mode-card">
          <h2>儿童自主阅读</h2>
          <p>桌面家长端只负责授权绘本范围、阅读时长和会话交接，不渲染儿童端界面。</p>
          <button
            class="secondary"
            type="button"
            :disabled="session.status !== 'CREATED' || session.mode !== 'PARENT_CHILD' || turnBusy"
            @click="openChildMode"
          >
            {{ session.mode === 'CHILD_SOLO' ? '此会话已开启儿童模式' : session.status === 'CREATED' ? '设置并进入儿童模式' : '本次阅读已开始，不能切换模式' }}
          </button>
        </section>
        <section v-if="report" class="side-card">
          <h2>本次共读小结</h2>
          <p v-if="report.status === 'PENDING'" role="status">小结正在生成，以下是已保存的阅读记录。</p>
          <p v-else-if="report.status === 'FAILED'" role="status">小结生成失败，已保留本次阅读记录。</p>
          <p v-else-if="report.status === 'READY'" role="status">小结已生成。</p>
          <p class="address">{{ report.summary }}</p>
          <button v-if="report.status === 'PENDING'" class="secondary" type="button" :disabled="turnBusy" @click="refreshProgress">刷新小结</button>
        </section>
      </aside>
    </div>

    <div v-if="voiceOpen" class="modal-backdrop" @click.self="closeVoice">
      <section class="modal-card voice-card" role="dialog" aria-modal="true" aria-labelledby="voice-title">
        <div class="modal-head">
          <div><h2 id="voice-title">本页问题</h2><p>{{ book.title }} · 第 {{ currentPage?.pageNumber }} 页</p></div>
          <button class="modal-close" type="button" aria-label="关闭本页问题" @click="closeVoice">×</button>
        </div>
        <p v-if="voiceError" class="form-error" role="alert">{{ voiceError }}</p>
        <template v-if="transcript && voiceState !== 'starting'">
          <div class="transcript-box">
            <template v-if="transcript.status === 'draft'">
              <label for="voice-transcript">{{ transcript.source === 'typed' ? '输入问题' : '待确认的转写' }}</label>
              <p id="transcript-hint">请核对孩子说的话，确认前可以修改文字。</p>
              <textarea id="voice-transcript" ref="transcriptEditor" v-model="transcript.text" aria-describedby="transcript-hint" rows="3"></textarea>
            </template>
            <template v-else>
              <h3 role="status">{{ transcript.source === 'typed' ? '已确认的文字' : transcript.status === 'confirmed' ? '已确认的转写' : '识别到的问题' }}</h3>
              <p>{{ transcript.text }}</p>
            </template>
          </div>
          <p>文字仅在本次弹窗内查看，关闭或换页后清除。互动问答尚未接入。</p>
          <div class="modal-actions">
            <button class="secondary" type="button" @click="toggleRecording">{{ transcript.source === 'typed' ? '改用录音' : '重新录音' }}</button>
            <button v-if="transcript.status === 'draft'" class="primary" type="button" :disabled="!transcript.text.trim()" @click="confirmTranscript">{{ transcript.source === 'typed' ? '确认文字' : '确认转写' }}</button>
            <button v-else class="primary" type="button" @click="closeVoice">关闭</button>
          </div>
        </template>
        <template v-else>
          <div class="voice-center">
            <button class="mic-button" :class="{ recording: voiceState === 'recording' }" :disabled="['starting', 'processing'].includes(voiceState)" :aria-label="voiceState === 'recording' ? '停止并识别' : '开始录音'" type="button" @click="toggleRecording">
              {{ voiceState === 'recording' ? '■' : '◉' }}
            </button>
            <h3>{{ voiceState === 'recording' ? '正在录音，点停止后识别' : voiceState === 'starting' ? '正在申请麦克风' : voiceState === 'processing' ? '正在识别' : '点一下，说出你的问题' }}</h3>
            <p v-if="!voiceError">点击“停止并识别”会上传本段录音，识别结果将在这里显示。取消会丢弃录音。</p>
          </div>
          <button v-if="['idle', 'error', 'unavailable'].includes(voiceState)" class="secondary" type="button" @click="editQuestionText">直接输入文字</button>
          <button class="secondary" type="button" @click="closeVoice">取消并关闭</button>
        </template>
      </section>
    </div>

    <div v-if="childModeOpen" class="modal-backdrop" @click.self="childModeOpen = false">
      <section class="modal-card" role="dialog" aria-modal="true" aria-labelledby="child-mode-title">
        <div class="modal-head">
          <div><h2 id="child-mode-title">交接给儿童自主阅读</h2><p>开启后当前浏览器会切换为受限儿童会话，家长导航和档案将不可访问。</p></div>
          <button class="modal-close" type="button" aria-label="关闭儿童模式设置" :disabled="childModeBusy" @click="childModeOpen = false">×</button>
        </div>
        <div class="form-field">
          <span>本次阅读时长</span>
          <select v-model.number="childMinutes" :disabled="childModeBusy">
            <option :value="5">5 分钟</option>
            <option :value="10">10 分钟</option>
            <option :value="15">15 分钟</option>
            <option :value="20">20 分钟</option>
            <option :value="30">30 分钟</option>
          </select>
        </div>
        <div class="form-field">
          <span>授权绘本范围</span>
          <div class="child-book-range">
            <label v-for="book in childBookChoices" :key="book.storyVersionId">
              <input
                type="checkbox"
                :checked="childAllowedVersions.includes(book.storyVersionId)"
                :disabled="childModeBusy || book.storyVersionId === props.book?.storyVersionId"
                @change="toggleChildBook(book.storyVersionId)"
              >
              <span>{{ book.title }}</span>
            </label>
          </div>
          <small>当前正在阅读的绘本必须保留在授权范围内。</small>
        </div>
        <p class="child-handoff-modal-note">儿童端可能是竖版 Web、横版硬件，或在其他设备中使用受控会话；本桌面页只完成家长授权与交接。</p>
        <div class="modal-actions">
          <button class="secondary" type="button" :disabled="childModeBusy" @click="childModeOpen = false">取消</button>
          <button class="primary" type="button" :disabled="childModeBusy || !childAllowedVersions.length" @click="submitChildMode">
            {{ childModeBusy ? '正在开启…' : '开启儿童模式' }}
          </button>
        </div>
      </section>
    </div>
  </main>
  <main v-else class="reader-page page-width">
    <div class="empty-state"><div><h2>阅读会话暂时不可用</h2><p>请返回绘本馆，登录并重新开始阅读。</p><button class="primary" type="button" @click="emit('library')">返回绘本馆</button></div></div>
  </main>
</template>
