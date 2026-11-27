<script setup>
import { computed } from 'vue'
import { G10_STATUS_LABELS, STRATEGY_LABELS } from '../api/g10'

const props = defineProps({
  result: { type: Object, default: null },
  followup: Boolean,
  quiet: Boolean,
  revision: { type: Number, required: true },
  pending: { type: Boolean, default: false },
  demo: { type: Boolean, default: false },
  questionReady: { type: Boolean, default: false },
  childMode: Boolean,
  childAnswerOptions: { type: Array, default: null },
  canSkip: { type: Boolean, default: false },
  answerAudioUrl: { type: String, default: '' },
  answerAudioLoading: { type: Boolean, default: false },
})
const emit = defineEmits(['generate', 'answer', 'understood', 'not-understood', 'skip', 'play-answer', 'select-answer', 'reload-answer-options'])

const statusLabel = computed(() => G10_STATUS_LABELS[props.result?.status] ?? '')
const strategyLabel = computed(() => STRATEGY_LABELS[props.result?.strategyType] ?? '')
const hasOutput = computed(() => Boolean(props.result?.outputText))
</script>

<template>
  <section class="interaction-card" aria-labelledby="interaction-title">
    <div class="section-heading">
      <div>
        <p class="eyebrow">亲子互动</p>
        <h2 id="interaction-title">一起说一说</h2>
      </div>
      <span v-if="demo" class="mode-tag">演示结果</span>
    </div>

    <div v-if="!quiet && result && !followup" class="agent-answer">
      <div class="answer-chips">
        <span v-if="statusLabel">{{ statusLabel }}</span>
        <span v-if="strategyLabel">{{ strategyLabel }}</span>
      </div>
      <p v-if="result.strategyType === 'SKIP' || result.feedback === 'SKIPPED'" class="agent-text">已跳过本页互动。</p>
      <p v-else-if="hasOutput" class="agent-text">{{ result.outputText }}</p>
      <p v-else class="muted">本次没有生成可展示的文字。</p>
      <ul v-if="result.sources?.length" class="source-list">
        <li v-for="source in result.sources" :key="`${source.knowledgeItemId}-${source.sourceTitle}`">
          {{ source.sourceTitle }}
        </li>
      </ul>
      <button
        v-if="!demo && questionReady && result.interactionEventId && result.revision === revision"
        class="button button-ghost button-small"
        type="button"
        :disabled="pending || answerAudioLoading"
        @click="emit('play-answer')"
      >
        {{ answerAudioLoading ? '音频准备中…' : '播放回答音频' }}
      </button>
      <p v-else class="muted source-note">当前没有可播放的回答音频。</p>
    </div>
    <p v-else-if="!quiet && !followup" class="muted">
      尚未获取本页共读问题。生成后会展示 G10 的实际返回结果，不使用固定互动次数或能力评价。
    </p>

    <slot />
    <div v-if="!quiet && result && followup" class="agent-answer">
      <p class="agent-text"><strong>芽芽追问：</strong>{{ result.outputText }}</p>
    </div>

    <div class="interaction-actions">
      <button v-if="!quiet && !questionReady" class="button button-primary" type="button" :disabled="pending" @click="emit('generate')">
        {{ pending ? '生成中…' : '生成本页共读问题' }}
      </button>
      <template v-if="childMode">
        <button v-for="option in childAnswerOptions" :key="option" class="button button-soft" type="button"
          :disabled="pending" @click="emit('select-answer', option)">{{ option }}</button>
        <button v-if="questionReady && childAnswerOptions === null" class="button button-ghost" type="button"
          :disabled="pending" @click="emit('reload-answer-options')">重新获取答题选项</button>
        <p v-else-if="!pending && !childAnswerOptions?.length" class="muted">本页暂无可提交的答题选项</p>
      </template>
      <button v-else class="button button-soft" type="button" :disabled="pending || (!quiet && Boolean(result) && !questionReady)" @click="emit('answer')">
        孩子回答
      </button>
      <button v-if="!quiet" class="button button-soft" type="button" :disabled="pending || !questionReady" @click="emit('not-understood')">
        没听懂
      </button>
      <button v-if="!quiet" class="button button-soft" type="button" :disabled="pending || !questionReady" @click="emit('understood')">已听懂</button>
      <button class="button button-ghost" type="button" :disabled="!canSkip" @click="emit('skip')">
        跳过
      </button>
    </div>
  </section>
</template>
