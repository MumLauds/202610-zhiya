<script setup>
import { ref } from 'vue'
import { describeApiError } from '../api/client'

const props = defineProps({
  health: { type: String, default: 'checking' },
  login: { type: Function, required: true },
  register: { type: Function, required: true },
})

const emit = defineEmits(['back'])
const publicSite = import.meta.env.MODE === 'public'
const mode = ref('login')
const account = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  if (busy.value) return
  error.value = ''
  if (!account.value.trim() || password.value.length < 8) {
    error.value = '请输入账号和至少 8 位密码'
    return
  }
  busy.value = true
  try {
    await props[mode.value](account.value.trim(), password.value)
  } catch (failure) {
    if (failure.code === 'ACCOUNT_CREATED_LOGIN_FAILED') {
      mode.value = 'login'
      password.value = ''
      return
    }
    error.value = describeApiError(failure)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="auth-page page-width">
    <section class="auth-scene">
      <div class="auth-copy">
        <span class="eyebrow">亲子共读 · 家长空间</span>
        <h1>把每个普通夜晚，读成一段故事</h1>
        <p>登录后读取组内已发布的绘本、儿童档案和真实阅读进度。</p>
        <button v-if="!publicSite" class="text-button" type="button" @click="emit('back')">先看看本地设计预览 →</button>
      </div>
      <form class="auth-card" @submit.prevent="submit">
        <div class="auth-tabs">
          <button type="button" :disabled="busy" :class="{ active: mode === 'login' }" @click="mode = 'login'">登录</button>
          <button type="button" :disabled="busy" :class="{ active: mode === 'register' }" @click="mode = 'register'">注册</button>
        </div>
        <h2>{{ mode === 'login' ? '回到知芽' : '创建家长账号' }}</h2>
        <p class="auth-note">家长登录后创建儿童资料；内容管理员账号登录后直接进入内容工作台。</p>
        <label class="form-field">
          <span>账号</span>
          <input v-model="account" autocomplete="username" placeholder="3–64 个字符">
        </label>
        <label class="form-field">
          <span>密码</span>
          <input v-model="password" type="password" :autocomplete="mode === 'login' ? 'current-password' : 'new-password'" placeholder="至少 8 位">
        </label>
        <p v-if="error" class="form-error">{{ error }}</p>
        <p v-if="health === 'offline'" class="form-error">暂时无法连接服务，请稍后重试。</p>
        <button class="primary auth-submit" type="submit" :disabled="busy">
          {{ busy ? '正在连接…' : mode === 'login' ? (publicSite ? '登录知芽' : '登录家长空间') : '注册并登录' }}
        </button>
      </form>
    </section>
  </main>
</template>
