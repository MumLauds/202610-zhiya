<script setup>
defineProps({
  tokens: { type: Array, default: () => [] },
  showPinyin: { type: Boolean, default: true },
  highlightIndex: { type: Number, default: -1 },
})
</script>

<template>
  <p class="reading-text" :aria-label="tokens.map((token) => token.char).join('')">
    <span
      v-for="(token, index) in tokens"
      :key="`${index}-${token.char}`"
      class="reading-token"
      :class="{ 'is-highlighted': index === highlightIndex, 'is-punctuation': !token.pinyin }"
    >
      <small v-if="showPinyin && token.pinyin" aria-hidden="true">{{ token.pinyin }}</small>
      <b>{{ token.char }}</b>
    </span>
  </p>
</template>
