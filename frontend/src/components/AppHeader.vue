<script setup>
import { computed } from 'vue'

const props = defineProps({
  route: { type: Object, required: true },
  auth: { type: Object, required: true },
})

const emit = defineEmits(['navigate'])

const readerItems = [
  { label: '首页', path: 'home', icon: '/assets/figma-prototype/icon-house.svg' },
  { label: '绘本馆', path: 'library', icon: '/assets/figma-prototype/icon-bookmark.svg' },
  { label: '创作工坊', path: 'workshop', icon: '/assets/figma-prototype/icon-spark.svg' },
  { label: '成长记录', path: 'account/growth', icon: '/assets/figma-prototype/icon-clock.svg' },
  { label: '个人中心', path: 'account/overview', icon: '/assets/figma-prototype/icon-users.svg' },
]
const items = computed(() => props.auth.role === 'ADMIN'
  ? [
      { label: '首页', path: 'home', icon: '/assets/figma-prototype/icon-house.svg' },
      { label: '内容管理', path: 'admin/storybooks', icon: '/assets/figma-prototype/icon-bookmark.svg' },
    ]
  : readerItems)

function current(path) {
  if (path === 'home') return ['home'].includes(props.route.name)
  if (path.startsWith('admin')) return props.route.name === 'admin'
  if (path === 'library') return ['library', 'detail', 'reader'].includes(props.route.name)
  if (path === 'workshop') return props.route.name === 'workshop'
  if (path === 'account/growth') return props.route.name === 'account' && props.route.section === 'growth'
  return props.route.name === 'account' && props.route.section !== 'growth'
}
</script>

<template>
  <header class="site-header">
    <div class="page-width header-inner">
      <button class="brand-button" type="button" aria-label="返回知芽绘本首页" @click="emit('navigate', 'home')">
        <span class="brand-mark" aria-hidden="true"></span>
        <span class="brand-copy"><strong>知芽绘本</strong><small>AI 亲子共读</small></span>
      </button>
      <nav class="top-nav" aria-label="主导航">
        <button
          v-for="item in items"
          :key="item.path"
          class="nav-button"
          :class="{ active: current(item.path) }"
          :aria-current="current(item.path) ? 'page' : undefined"
          type="button"
          @click="emit('navigate', item.path)"
        >
          <img class="nav-icon" :src="item.icon" alt="">
          <span>{{ item.label }}</span>
        </button>
      </nav>
      <div class="header-spacer" />
      <button class="avatar-button" type="button" aria-label="打开账号" @click="emit('navigate', auth.authenticated ? (auth.role === 'ADMIN' ? 'admin/storybooks' : 'account/overview') : 'auth')">
        {{ auth.nickname?.slice(0, 1) || '知' }}
      </button>
    </div>
  </header>
</template>
