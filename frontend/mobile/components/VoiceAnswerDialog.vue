<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { useVoiceRecorder } from '../composables/useVoiceRecorder'

const props = defineProps({
  voiceApi: { type: Object, required: true },
  sessionId: { type: Number, required: true },
  pageNumber: { type: Number, required: true },
  revision: { type: Number, required: true },
  demo: { type: Boolean, default: false },
  submitting: { type: Boolean, default: false },
  initialText: { type: String, default: '' },
  submitError: { type: String, default: '' },
})
const emit = defineEmits(['close', 'confirmed'])

const recorder = useVoiceRecorder()
const transcript = ref(props.initialText)
const reliable = ref(null)
const manualMode = ref(Boolean(props.initialText))
const elapsedSeconds = ref(0)
let timer = null
let requestController = null

watch(recorder.state, (state) => {
  clearInterval(timer)
  timer = null
  if (state === 'recording') {
    elapsedSeconds.value = 0
    timer = setInterval(() => { elapsedSeconds.value += 1 }, 1000)
  }
})

onBeforeUnmount(() => {
  clearInterval(timer)
  requestController?.abort()
})

function close() {
  if (props.submitting) return
  requestController?.abort()
  recorder.cancel()
  emit('close')
}

async function toggleRecording() {
  if (recorder.recording.value) {
    await recorder.stop()
    return
  }
  try {
    await recorder.start()
  } catch {
    manualMode.value = true
  }
}

async function submitTranscription() {
  if (!recorder.blob.value) return
  recorder.markTranscribing()
  requestController?.abort()
  const controller = new AbortController()
  requestController = controller
  try {
    const response = await props.voiceApi.transcribe({
      file: recorder.blob.value,
      readingSessionId: props.sessionId,
      pageNumber: props.pageNumber,
      revision: props.revision,
      signal: controller.signal,
    })
    if (controller.signal.aborted) return
    transcript.value = response.data?.text ?? ''
    reliable.value = response.data?.reliable ?? null
    if (!transcript.value) {
      manualMode.value = true
      recorder.markError(response.data?.message || '没有识别到清晰内容，请手动输入或重新录制')
      return
    }
    recorder.markReview({ text: transcript.value, reliable: reliable.value })
  } catch (error) {
    if (controller.signal.aborted || error?.code === 'REQUEST_ABORTED') return
    recorder.markError(error?.message ?? '转写失败，请重录或手动输入')
    manualMode.value = true
  } finally {
    if (requestController === controller) requestController = null
  }
}

function useManualInput() {
  requestController?.abort()
  manualMode.value = true
  recorder.cancel()
  transcript.value = ''
  reliable.value = null
}

function confirmAnswer() {
  const text = transcript.value.trim()
  if (!text || props.submitting) return
  emit('confirmed', { text, reliable: reliable.value })
}

function formatDuration(seconds) {
  return `00:${String(seconds).padStart(2, '0')}`
}
</script>

<template>
  <div class="dialog-backdrop" role="presentation" @click.self="close">
    <section class="answer-dialog" role="dialog" aria-modal="true" aria-labelledby="answer-title">
      <header class="dialog-header">
        <div>
          <p class="eyebrow">G08 语音转写</p>
          <h2 id="answer-title">听听孩子的回答</h2>
        </div>
        <button class="icon-button" type="button" aria-label="关闭" :disabled="submitting" @click="close">×</button>
      </header>

      <p class="dialog-copy">
        {{ demo ? '当前为演示转写，结果不会写入后端。' : '录音仅用于本次转写；家长确认后才把本轮互动反馈提交给会话。' }}
      </p>

      <div v-if="!manualMode && recorder.state.value !== 'review'" class="recorder-panel">
        <button
          class="record-button"
          :class="{ 'is-recording': recorder.recording.value }"
          type="button"
          :disabled="recorder.busy.value"
          @click="toggleRecording"
        >
          <span class="record-dot" />
          {{ recorder.recording.value ? `停止录音 ${formatDuration(elapsedSeconds)}` : recorder.state.value === 'requesting' ? '等待麦克风权限…' : '开始录音' }}
        </button>

        <audio v-if="recorder.objectUrl.value" class="record-preview" :src="recorder.objectUrl.value" controls />

        <div v-if="recorder.state.value === 'recorded'" class="dialog-actions">
          <button class="button button-primary" type="button" @click="submitTranscription">提交转写</button>
          <button class="button button-ghost" type="button" @click="recorder.cancel">重录</button>
          <button class="button button-ghost" type="button" @click="useManualInput">手动输入</button>
        </div>

        <p v-if="recorder.state.value === 'transcribing'" class="status-line">
          <span class="spinner" /> 正在识别语音，请稍候…
        </p>
      </div>

      <div v-if="manualMode || recorder.state.value === 'review'" class="review-panel">
        <label for="transcript">家长确认文本</label>
        <textarea
          id="transcript"
          v-model="transcript"
          :disabled="submitting"
          rows="4"
          maxlength="200"
          placeholder="可以修改识别结果，或直接输入孩子的回答"
        />
        <p v-if="reliable === false" class="warning-note">识别结果未标记为可靠，请家长仔细核对。</p>
        <p v-if="recorder.error.value" class="inline-error">{{ recorder.error.value }}</p>
        <p v-if="submitError" class="inline-error" role="alert">{{ submitError }}</p>
        <div class="dialog-actions">
          <button class="button button-primary" type="button" :disabled="submitting || !transcript.trim()" @click="confirmAnswer">
            {{ submitting ? '正在提交…' : '确认回答' }}
          </button>
          <button class="button button-ghost" type="button" :disabled="submitting" @click="manualMode = false; recorder.cancel(); transcript = ''">
            重新录制
          </button>
        </div>
      </div>

      <p v-if="recorder.error.value && !manualMode && recorder.state.value !== 'review'" class="inline-error">
        {{ recorder.error.value }}
      </p>
      <button v-if="!manualMode && (recorder.state.value === 'idle' || recorder.recording.value || recorder.busy.value)" class="text-button" type="button" @click="useManualInput">
        改用文字输入
      </button>
    </section>
  </div>
</template>

