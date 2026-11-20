<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { describeApiError } from '../api/client'

const props = defineProps({
  childMode: { type: Object, default: null },
  session: { type: Object, default: null },
  book: { type: Object, default: null },
  auth: { type: Object, required: true },
  loading: { type: Boolean, default: false },
  identityLoading: { type: Boolean, default: false },
  identityError: { type: String, default: '' },
  unlockAction: { type: Function, required: true },
})
const emit = defineEmits(['recheck-identity'])
const verifying = computed(() => ['checking', 'verifying-handoff'].includes(props.auth.status))

const account = ref(props.auth.account || '')
const password = ref('')
const error = ref('')
const unlocking = ref(false)
const now = ref(Date.now())
let timer = null

const remaining = computed(() => {
  if (!props.childMode?.expiresAt) return '以儿童端显示为准'
  const minutes = Math.max(0, Math.ceil((new Date(props.childMode.expiresAt).getTime() - now.value) / 60000))
  return `${minutes} 分钟`
})

async function unlock() {
  if (unlocking.value || !account.value.trim() || password.value.length < 8) {
    error.value = '请输入家长账号和至少 8 位密码'
    return
  }
  error.value = ''
  unlocking.value = true
  try {
    const unlocked = await props.unlockAction(account.value.trim(), password.value)
    if (unlocked === false) error.value = '家长解锁失败，请检查账号和密码后重试。'
    else password.value = ''
  } catch (reason) {
    error.value = describeApiError(reason)
  } finally {
    unlocking.value = false
  }
}

onMounted(() => {
  timer = window.setInterval(() => { now.value = Date.now() }, 30000)
})
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <main class="child-handoff-page">
    <section class="child-handoff-card">
      <div class="child-mode-mark" aria-hidden="true">芽</div>
      <p class="eyebrow">儿童自主阅读模式</p>
      <h1 v-if="verifying">正在核对设备身份</h1>
      <h1 v-else>{{ auth.status === 'handoff' ? '请家长解锁设备' : '阅读空间已为孩子准备好' }}</h1>
      <p v-if="book" class="child-handoff-book">当前绘本：{{ book.title }}</p>
      <p v-if="!verifying && auth.status !== 'handoff'">
        本次剩余：{{ remaining }}
        <template v-if="Number.isInteger(childMode?.allowedStoryVersionCount)">
          · 已授权 {{ childMode.allowedStoryVersionCount }} 个绘本版本
        </template>
      </p>
      <div class="child-handoff-notice">
        这是桌面家长端的授权与交接页。儿童阅读交互由手机端或 4.3C 硬件端模块承接；本页面不会展示家长导航、档案或历史记录。
      </div>
      <div v-if="verifying" class="child-handoff-loading">
        <p v-if="identityError" role="alert">{{ identityError }}</p>
        <button class="secondary" type="button" :disabled="identityLoading" @click="emit('recheck-identity')">{{ identityLoading ? '正在读取设备身份…' : '重新核对设备身份' }}</button>
      </div>
      <p v-else-if="loading" class="child-handoff-loading" role="status">正在核对当前儿童授权的会话信息…</p>
      <form v-else class="child-handoff-unlock" @submit.prevent="unlock">
        <h2>家长解锁</h2>
        <label class="form-field"><span>家长账号</span><input v-model="account" autocomplete="username"></label>
        <label class="form-field"><span>密码</span><input v-model="password" type="password" autocomplete="current-password"></label>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <button class="primary" type="submit" :disabled="unlocking">{{ unlocking ? '正在解锁…' : '退出儿童模式' }}</button>
      </form>
    </section>
  </main>
</template>
