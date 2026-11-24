<script setup>
defineProps({
  tokens: { type: Array, default: () => [] },
  showPinyin: { type: Boolean, default: true },
  highlightIndex: { type: Number, default: -1 },
  playable: { type: Boolean, default: false },
})
const emit = defineEmits(['play'])
</script>

<template>
  <component
    :is="playable ? 'button' : 'p'"
    class="reading-text"
    :class="{ 'reading-text--playable': playable }"
    :type="playable ? 'button' : undefined"
    :aria-label="(playable ? '朗读本页：' : '') + tokens.map((token) => token.char).join('')"
    @click="playable && emit('play')"
  >
    <span
      v-for="(token, index) in tokens"
      :key="`${index}-${token.char}`"
      class="reading-token"
      :class="{ 'is-highlighted': index === highlightIndex, 'is-punctuation': !token.pinyin }"
    >
      <small v-if="showPinyin && token.pinyin" aria-hidden="true">{{ token.pinyin }}</small>
      <b>{{ token.char }}</b>
    </span>
  </component>
</template>
