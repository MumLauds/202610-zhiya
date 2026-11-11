<script setup>
import { onMounted, ref } from 'vue'
import { checkHealth } from './api/health'

const sections = [
  { id: 'home', title: '家长端', description: '亲子共读入口建设中。' },
  { id: 'library', title: '绘本馆', description: '绘本审核并发布后将在这里展示。' },
  { id: 'reading', title: '共读', description: '阅读页面建设中。' },
  { id: 'profile', title: '个人中心', description: '账号与阅读档案功能建设中。' },
]

const section = ref(sections[0])
const status = ref('检查中')
const checking = ref(false)

function selectSection() {
  section.value = sections.find(({ id }) => location.hash === '#' + id) ?? sections[0]
}

async function refreshHealth() {
  checking.value = true
  status.value = '检查中'
  try {
    status.value = await checkHealth() ? '后端已就绪' : '后端暂不可用'
  } catch {
    status.value = '后端暂不可用'
  } finally {
    checking.value = false
  }
}

onMounted(() => {
  selectSection()
  window.addEventListener('hashchange', selectSection)
  refreshHealth()
})
</script>

<template>
  <header class="site-header">
    <div class="shell header-content">
      <a class="brand" href="#home">知芽</a>
      <nav aria-label="主导航">
        <a v-for="item in sections" :key="item.id" :href="'#' + item.id"
           :aria-current="section.id === item.id ? 'page' : undefined">
          {{ item.title }}
        </a>
      </nav>
    </div>
  </header>
  <main class="shell">
    <section class="panel">
      <h1>{{ section.title }}</h1>
      <p>{{ section.description }}</p>
    </section>
    <div class="service-status">
      <span role="status">{{ status }}</span>
      <button type="button" :disabled="checking" @click="refreshHealth">重新检查</button>
    </div>
  </main>
</template>
