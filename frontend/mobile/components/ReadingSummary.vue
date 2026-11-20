<script setup>
defineProps({
  book: { type: Object, required: true },
  session: { type: Object, required: true },
  report: { type: Object, default: null },
})
defineEmits(['exit'])
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
        <dd>{{ session.visitedPageNumbers?.length ?? 0 }}</dd>
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
      <p v-if="report?.summary">{{ report.summary }}</p>
      <p v-else class="muted">当前没有可展示的小结内容。</p>
      <ul v-if="report?.activities?.length">
        <li v-for="(activity, index) in report.activities" :key="index">{{ activity }}</li>
      </ul>
    </div>

    <button class="button button-primary button-wide" type="button" @click="$emit('exit')">返回绘本馆</button>
  </section>
</template>
