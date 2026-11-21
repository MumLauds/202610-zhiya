<script setup>
import { onMounted, ref } from 'vue'
import App from './App.vue'
import { parseMobileRoute } from '../src/data/mobileRoute'

const state = ref('checking')
async function checkSession() {
  state.value = 'checking'
  try {
    const response = await fetch('/api/v1/storybooks?pageSize=1', { cache: 'no-store' })
    if (response.status === 401) {
      const route = parseMobileRoute(window.location.hash)
      const params = new URLSearchParams({ entry: 'mobile' })
      if (route.hash) params.set('mobileRoute', route.hash)
      window.location.replace(`/?${params}#auth`)
      return
    }
    state.value = response.ok ? 'ready' : 'unavailable'
  } catch {
    state.value = 'unavailable'
  }
}
onMounted(checkSession)
</script>

<template>
  <App v-if="state === 'ready'" public-site />
  <main v-else class="mobile-main">
    <p v-if="state === 'checking'" role="status">正在连接书架…</p>
    <template v-else>
      <p role="alert">暂时无法读取书架，请稍后重试。</p>
      <button type="button" class="button button-primary" @click="checkSession">重新连接</button>
    </template>
  </main>
</template>
