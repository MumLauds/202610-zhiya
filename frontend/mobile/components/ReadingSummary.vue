<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { countReadPages } from '../../src/data/readingProgress'
const props = defineProps({
  book: { type: Object, required: true },
  session: { type: Object, required: true },
  report: { type: Object, default: null },
  sessionApi: { type: Object, default: null },
})
const emit = defineEmits(['exit', 'update:report'])
const refreshing = ref(false)
const refreshError = ref('')
const controller = new AbortController()
onBeforeUnmount(() => controller.abort())

const activities = computed(() => {
  if (props.report?.status !== 'READY' || !Array.isArray(props.report.activities)) return []
  return props.report.activities.filter(activity => {
    if (typeof activity === 'string') return activity.trim().length > 0
    return typeof activity?.name === 'string' && activity.name.trim()
      && Array.isArray(activity.materials) && activity.materials.every(item => typeof item === 'string')
      && Array.isArray(activity.steps) && activity.steps.every(step => typeof step === 'string')
  })
})

async function refreshReport() {
  if (refreshing.value || !props.sessionApi || props.report?.status !== 'PENDING') return
  refreshing.value = true
  refreshError.value = ''
  try {
    const id = props.session.readingSessionId
    const { data } = await props.sessionApi.get(id, { signal: controller.signal })
    if (controller.signal.aborted) return
    if (data?.session?.readingSessionId !== id) throw new Error('未能读回当前会话的小结，请重试。')
    emit('update:report', data.report ?? null)
  } catch (error) {
    if (!controller.signal.aborted) refreshError.value = error.message || '刷新小结失败，请重试。'
  } finally {
    refreshing.value = false
  }
}
</script>

<template>
  <section class="summary-panel">
    <span class="summary-orbit" aria-hidden="true">✓</span>
    <p class="eyebrow">本次阅读已结束</p>
    <h1>{{ book.title }}</h1>
    <p class="summary-copy">只展示本次会话实际产生的进度与小结，不补充未经接口验证的能力评价。</p>

    <dl class="summary-facts">
      <div>
        <dt>访问页数</dt>
        <dd>{{ countReadPages(session) }}</dd>
      </div>
      <div>
        <dt>会话修订</dt>
        <dd>{{ session.revision }}</dd>
      </div>
      <div>
        <dt>小结状态</dt>
        <dd>{{ report?.status ?? '暂无' }}</dd>
      </div>
    </dl>

    <div class="summary-report">
      <h2>阅读小结</h2>
      <p v-if="report?.status === 'PENDING'" role="status">小结正在生成。</p>
      <p v-else-if="report?.status === 'FAILED'" role="status">小结生成失败，已保留本次阅读记录。</p>
      <p v-if="report?.summary">{{ report.summary }}</p>
      <p v-else class="muted">当前没有可展示的小结内容。</p>
      <template v-for="(activity, index) in activities" :key="index">
        <p v-if="typeof activity === 'string'">{{ activity }}</p>
        <section v-else>
          <h3>亲子活动 · {{ activity.name }}</h3>
          <p v-if="activity.materials.length">准备材料：{{ activity.materials.join('、') }}</p>
          <template v-if="activity.steps.length">
            <h4>活动步骤</h4>
            <ol>
              <li v-for="(step, position) in activity.steps" :key="position">{{ step }}</li>
            </ol>
          </template>
        </section>
      </template>
      <p v-if="refreshError" class="inline-error" role="alert">{{ refreshError }}</p>
      <button v-if="sessionApi && report?.status === 'PENDING'" type="button" class="button button-ghost" :disabled="refreshing" @click="refreshReport">
        {{ refreshing ? '正在刷新…' : '刷新小结' }}
      </button>
    </div>

    <button class="button button-primary button-wide" type="button" @click="$emit('exit')">返回绘本馆</button>
  </section>
</template>
