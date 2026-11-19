<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { describeApiError } from '../api/client'
import { ageLabels } from '../data/preview'

const props = defineProps({
  section: { type: String, default: 'overview' },
  auth: { type: Object, required: true },
  profiles: { type: Array, default: () => [] },
  profilesError: { type: String, default: '' },
  profilesLoading: { type: Boolean, default: false },
  activeProfileId: { type: Number, default: null },
  sessions: { type: Array, default: () => [] },
  sessionsError: { type: String, default: '' },
  books: { type: Array, default: () => [] },
  totals: { type: Object, required: true },
  totalsLoaded: { type: Boolean, default: true },
  loading: { type: Boolean, default: false },
  saveProfileAction: { type: Function, required: true },
  deleteProfileAction: { type: Function, default: async () => false },
  deleteSessionAction: { type: Function, default: async () => false },
  checkDeletionAction: { type: Function, default: async () => 'unconfirmed' },
  pendingDeletionChecks: { type: Object, default: () => ({}) },
  reloadAccountDataAction: { type: Function, default: async () => false },
})

const emit = defineEmits(['navigate', 'select-profile', 'sign-out', 'start', 'reload-sessions'])
const editing = ref(false)
const saving = ref(false)
const saveError = ref('')
const deleteTarget = ref(null)
const deleteBusy = ref(false)
const deleteError = ref('')
const emptyProfile = { childProfileId: null, nickname: '', ageBand: 'AGE_4_5', interests: '', preferredMinutes: 15 }
const form = reactive({ ...emptyProfile })
const plan = reactive(JSON.parse(localStorage.getItem('zhiya.plan') || '{"minutes":15,"books":1,"weekly":5}'))

const nav = [
  ['overview', '个人中心首页', '⌂'],
  ['profile', '个人资料', '☺'],
  ['children', '儿童档案', '♧'],
  ['shelf', '我的书架', '▤'],
  ['growth', '成长记录', '⌁'],
  ['plan', '阅读计划', '▦'],
]
const activeProfile = computed(() => {
  if (props.activeProfileId === null) return props.profiles[0] || null
  return props.profiles.find((profile) => profile.childProfileId === props.activeProfileId) || null
})
const profileAction = computed(() => props.profiles.length ? '选择儿童档案' : '添加儿童档案')
const recentSessions = computed(() => props.sessions.slice(0, 4))
const unlistedDeleteIds = computed(() => [...(props.pendingDeletionChecks.session || [])].filter(
  id => !props.sessions.some(session => session.readingSessionId === id),
))
const profileDeletePending = computed(() => deleteBusy.value || props.pendingDeletionChecks.profile?.has(form.childProfileId))
watch(() => props.profiles, profiles => {
  if (editing.value && form.childProfileId !== null && !profiles.some(p => p.childProfileId === form.childProfileId)) {
    editing.value = false
    saveError.value = ''
    Object.assign(form, emptyProfile)
  }
})
const sessionStatusLabels = {
  CREATED: '未开始',
  IN_PROGRESS: '阅读中',
  FINISHED: '已完成',
  INTERRUPTED: '已中断',
}

function growthValue(metric) {
  if (props.loading) return '读取中'
  if (props.sessionsError || !props.totalsLoaded) return '待更新'
  return props.totals[metric]
}

function openNew() {
  if (saving.value) return
  saveError.value = ''
  Object.assign(form, emptyProfile)
  editing.value = true
}

function openEdit(profile) {
  if (saving.value) return
  saveError.value = ''
  Object.assign(form, {
    childProfileId: profile.childProfileId,
    nickname: profile.nickname || '',
    ageBand: profile.ageBand,
    interests: (profile.interests || []).join('、'),
    preferredMinutes: profile.preferredMinutes,
  })
  editing.value = true
}

async function saveProfile() {
  if (saving.value || profileDeletePending.value) return
  saveError.value = ''
  saving.value = true
  const payload = {
    nickname: form.nickname || null,
    ageBand: form.ageBand,
    interests: form.interests.split(/[、,，]/).map((item) => item.trim()).filter(Boolean),
    preferredMinutes: Number(form.preferredMinutes),
  }
  try {
    await props.saveProfileAction({ id: form.childProfileId, payload })
    editing.value = false
  } catch (error) {
    saveError.value = describeApiError(error)
  } finally {
    saving.value = false
  }
}

function savePlan() {
  localStorage.setItem('zhiya.plan', JSON.stringify(plan))
}

function saveLocalNickname() {
  localStorage.setItem('zhiya.guardianNickname', props.auth.nickname)
}

function sessionTitle(session) {
  return props.books.find((book) => book.storyVersionId === session.storyVersionId)?.title || `绘本期号 ${session.storyVersionId}`
}

function sessionRecordLabel(session) {
  return `阅读记录 #${session.readingSessionId}`
}

function askDeleteProfile(profile) {
  deleteError.value = ''
  deleteTarget.value = {
    type: 'profile',
    id: profile.childProfileId,
    needsCheck: props.pendingDeletionChecks.profile?.has(profile.childProfileId),
    title: `${profile.nickname || '未命名宝贝'}（档案 #${profile.childProfileId}）`,
  }
}

function askDeleteSession(session) {
  deleteError.value = ''
  deleteTarget.value = {
    type: 'session',
    id: session.readingSessionId,
    needsCheck: props.pendingDeletionChecks.session?.has(session.readingSessionId),
    title: session.storyVersionId ? `${sessionTitle(session)}（${sessionRecordLabel(session)}）` : sessionRecordLabel(session),
  }
}

async function confirmDelete() {
  const target = deleteTarget.value
  if (!target || deleteBusy.value) return
  deleteBusy.value = true
  deleteError.value = ''
  try {
    if (!target.needsCheck) {
      const remove = target.type === 'profile' ? props.deleteProfileAction : props.deleteSessionAction
      const result = target.confirmedUnlisted ? await remove(target.id, true) : await remove(target.id)
      if (result !== 'unconfirmed') {
        if (result) deleteTarget.value = null
        return
      }
      target.needsCheck = true
      target.confirmedUnlisted = false
    }
    const result = await props.checkDeletionAction(target.type, target.id)
    if (result === 'absent') {
      deleteTarget.value = null
    } else if (result === 'present') {
      target.needsCheck = false
      deleteError.value = '已核对，目标仍在列表中。如需删除，请再次确认。'
    } else if (result === 'unlisted') {
      target.needsCheck = false
      target.confirmedUnlisted = true
      deleteError.value = '已核对，这条记录仍保存在当前账号，但未显示在阅读历史中。如需删除，请再次确认。'
    } else {
      deleteError.value = '删除结果尚未确认，请重新核对。'
    }
  } catch (error) {
    deleteError.value = describeApiError(error)
  } finally {
    deleteBusy.value = false
  }
}
</script>

<template>
  <main class="account-page page-width">
    <div class="account-layout">
      <aside class="account-sidebar">
        <div class="account-person">
          <div class="account-avatar">{{ auth.nickname?.slice(0, 1) || '知' }}</div>
          <div><strong>{{ auth.nickname }}</strong><small>{{ auth.account || '本地预览账号' }}</small></div>
        </div>
        <button class="pill child-switch" type="button" @click="emit('navigate', 'account/children')">
          <span>{{ activeProfile ? `${activeProfile.nickname || '宝贝'} · ${ageLabels[activeProfile.ageBand] || '年龄段暂缺'}` : profileAction }}</span><span>⌄</span>
        </button>
        <nav class="account-nav" aria-label="个人中心功能">
          <button
            v-for="[key, label, glyph] in nav"
            :key="key"
            class="account-nav-button"
            :class="{ active: section === key }"
            type="button"
            @click="emit('navigate', `account/${key}`)"
          >
            <span class="account-nav-icon">{{ glyph }}</span>{{ label }}
          </button>
        </nav>
        <div class="account-side-note">真实账号、档案与阅读会话由后端提供。<br>收藏和计划暂存于本地。</div>
      </aside>

      <section class="account-content">
        <div v-if="profilesError" class="account-data-error" role="alert">
          <span>{{ profilesError }}</span>
          <button class="secondary" type="button" :disabled="profilesLoading" @click="reloadAccountDataAction">
            {{ profilesLoading ? '正在重新读取…' : '重新读取家长资料' }}
          </button>
        </div>
        <div v-if="sessionsError" class="account-data-error" role="alert">
          <span>阅读记录暂未更新。{{ sessionsError }}</span>
          <button class="secondary" type="button" :disabled="loading" @click="emit('reload-sessions')">重新读取阅读记录</button>
        </div>
        <template v-if="section === 'children'">
          <div class="account-title">
            <div><h1>我的宝贝</h1><p>记录孩子的成长，让每一次阅读都更有意义。</p></div>
            <button class="primary" type="button" :disabled="!auth.authenticated || profilesLoading || Boolean(profilesError)" @click="openNew">＋ 添加孩子</button>
          </div>
          <div v-if="profiles.length" class="child-grid">
            <article v-for="profile in profiles" :key="profile.childProfileId" class="account-card child-card" :class="{ active: profile.childProfileId === activeProfileId }">
              <div class="child-avatar">{{ profile.nickname?.slice(0, 1) || '芽' }}</div>
              <h2>{{ profile.nickname || '未命名宝贝' }}</h2>
              <p>档案 #{{ profile.childProfileId }} · {{ ageLabels[profile.ageBand] || '年龄段暂缺' }} · 每日 {{ profile.preferredMinutes }} 分钟</p>
              <div class="book-meta"><span v-for="interest in profile.interests" :key="interest">{{ interest }}</span></div>
              <div class="card-actions">
                <button class="secondary" type="button" @click="emit('select-profile', profile.childProfileId)">切换</button>
                <button class="secondary" type="button" @click="openEdit(profile)">编辑</button>
                <button class="danger" type="button" :disabled="deleteBusy || (saving && form.childProfileId === profile.childProfileId)" @click="askDeleteProfile(profile)">删除档案</button>
              </div>
            </article>
          </div>
          <div v-else-if="!profilesError" class="empty-state"><div><h2>还没有儿童档案</h2><p>登录后添加孩子，才能创建真实阅读会话。</p><button class="primary" type="button" @click="openNew">添加孩子</button></div></div>

          <form v-if="editing" class="account-card profile-form" @submit.prevent="saveProfile">
            <h2>{{ form.childProfileId ? '编辑档案' : '添加孩子' }}</h2>
            <label class="form-field"><span>昵称</span><input v-model="form.nickname" maxlength="32"></label>
            <label class="form-field"><span>年龄段</span>
              <select v-model="form.ageBand">
                <option v-for="(label, band) in ageLabels" :key="band" :value="band">{{ label }}</option>
              </select>
            </label>
            <label class="form-field"><span>兴趣</span><input v-model="form.interests" placeholder="用顿号分隔，例如：动物、自然"></label>
            <label class="form-field"><span>每日阅读分钟</span><input v-model.number="form.preferredMinutes" type="number" min="1" max="30"></label>
            <p v-if="saveError" class="form-error" role="alert">{{ saveError }}</p>
            <div class="modal-actions"><button class="secondary" type="button" :disabled="saving" @click="editing = false">取消</button><button class="primary" type="submit" :disabled="saving || profileDeletePending">{{ saving ? '正在保存…' : '保存档案' }}</button></div>
          </form>
        </template>

        <template v-else-if="section === 'growth'">
          <div class="account-title"><div><h1>成长足迹</h1><p>只展示后端真实阅读会话，不推断情绪或能力。</p></div></div>
          <div class="stats-grid">
            <div class="stat-card"><span>已完成/进行中会话</span><strong>{{ growthValue('sessions') }}</strong></div>
            <div class="stat-card"><span>读过绘本版本</span><strong>{{ growthValue('read') }}</strong></div>
            <div class="stat-card"><span>累计浏览页面</span><strong>{{ growthValue('pages') }}</strong></div>
          </div>
          <div v-if="loading" class="reader-loading"><div class="spinner" /></div>
          <div v-else-if="sessions.length" class="recent-list">
            <article v-for="session in sessions" :key="session.readingSessionId" class="recent-item">
              <div><strong>{{ sessionTitle(session) }}</strong><p>{{ sessionRecordLabel(session) }} · 浏览 {{ session.visitedPageNumbers.length }} 页 · {{ sessionStatusLabels[session.status] || '状态待确认' }}</p></div>
              <div class="recent-item-actions">
                <span class="pill">修订 {{ session.revision }}</span>
                <button class="danger" type="button" :disabled="deleteBusy" @click="askDeleteSession(session)">删除记录</button>
              </div>
            </article>
          </div>
          <div v-else-if="totalsLoaded && !sessionsError && !unlistedDeleteIds.length" class="empty-state"><div><h2>还没有真实阅读记录</h2><p>从绘本馆开始一次后端阅读后，这里会自动出现记录。</p><button class="primary" type="button" @click="emit('navigate', 'library')">去绘本馆</button></div></div>
          <div v-for="id in unlistedDeleteIds" :key="id" class="account-data-error">
            <span>阅读记录 #{{ id }} · 删除结果待确认</span>
            <button class="secondary" type="button" :disabled="deleteBusy" @click="askDeleteSession({ readingSessionId: id })">核对删除结果</button>
          </div>
        </template>

        <template v-else-if="section === 'shelf'">
          <div class="account-title"><div><h1>我的绘本小书架</h1><p>当前展示账号可访问的绘本；收藏持久化接口尚未接入。</p></div></div>
          <div class="shelf-grid">
            <article v-for="book in books" :key="book.id" class="shelf-card">
              <img :src="book.cover" :alt="`${book.title}封面`">
              <h2>{{ book.title }}</h2><p>{{ book.ageLabel }}</p>
              <button class="secondary" type="button" @click="emit('navigate', `detail/${book.storyVersionId}`)">查看详情</button>
            </article>
          </div>
        </template>

        <template v-else-if="section === 'plan'">
          <div class="account-title"><div><h1>阅读计划</h1><p>计划暂存于浏览器；后端计划接口尚未开放。</p></div></div>
          <form class="account-card plan-form" @submit.prevent="savePlan">
            <label class="form-field"><span>每日阅读时长</span><input v-model.number="plan.minutes" type="number" min="1" max="60"></label>
            <label class="form-field"><span>每日绘本数量</span><input v-model.number="plan.books" type="number" min="1" max="5"></label>
            <label class="form-field"><span>每周阅读天数</span><input v-model.number="plan.weekly" type="number" min="1" max="7"></label>
            <button class="primary" type="submit">保存本地计划</button>
          </form>
        </template>

        <template v-else-if="section === 'profile'">
          <div class="account-title"><div><h1>个人资料</h1><p>当前后端仅保存账号身份；昵称与签名仍保存在本地。</p></div></div>
          <section class="account-card profile-grid">
            <div class="profile-card-large"><div class="account-avatar">{{ auth.nickname?.slice(0, 1) || '知' }}</div><h2>{{ auth.nickname }}</h2><p>{{ auth.account || '未登录' }}</p></div>
            <form class="profile-form" @submit.prevent="saveLocalNickname">
              <label class="form-field"><span>昵称</span><input :value="auth.nickname" readonly></label>
              <label class="form-field"><span>登录账号</span><input :value="auth.account" readonly></label>
              <button class="secondary" type="button" :disabled="auth.status === 'signing-out'" @click="emit('sign-out')">{{ auth.status === 'signing-out' ? '正在退出…' : '退出登录' }}</button>
            </form>
          </section>
        </template>

        <template v-else>
          <section class="account-hero">
            <div class="account-hero-copy">
              <p class="eyebrow">今天也来陪孩子读一点</p>
              <h1>{{ activeProfile?.nickname || '孩子' }}的阅读空间</h1>
              <p v-if="!activeProfile">{{ profileAction }}后开始个性化阅读</p>
              <p v-else-if="activeProfile.interests?.length">{{ activeProfile.interests.join(' · ') }}</p>
              <button class="secondary" type="button" @click="emit('navigate', 'account/children')">管理儿童档案</button>
            </div>
            <div class="hero-illustration"><img src="/assets/reader-scene.svg" alt=""></div>
          </section>
          <div class="stats-grid">
            <div class="stat-card"><span>已完成/进行中会话</span><strong>{{ growthValue('sessions') }}</strong></div>
            <div class="stat-card"><span>访问绘本</span><strong>{{ growthValue('read') }}</strong></div>
            <div class="stat-card"><span>浏览页面</span><strong>{{ growthValue('pages') }}</strong></div>
            <div class="stat-card"><span>当前档案</span><strong>{{ activeProfile ? '已选择' : (profiles.length ? '待选择' : '待添加') }}</strong></div>
          </div>
          <section class="account-card">
            <div class="account-card-head"><h2>最近阅读</h2><button class="text-button" type="button" @click="emit('navigate', 'account/growth')">查看全部</button></div>
            <div v-if="recentSessions.length" class="recent-list">
              <article v-for="session in recentSessions" :key="session.readingSessionId" class="recent-item"><strong>{{ sessionTitle(session) }}</strong><span>第 {{ session.currentPage }} 页 · {{ sessionStatusLabels[session.status] || '状态待确认' }}</span></article>
            </div>
            <p v-else-if="totalsLoaded && !loading && !sessionsError" class="address">还没有真实阅读会话，从绘本馆开始一次亲子共读吧。</p>
          </section>
        </template>
      </section>
    </div>

    <div v-if="deleteTarget" class="modal-backdrop" @click.self="!deleteBusy && (deleteTarget = null)">
      <section class="modal-card confirm-card" role="dialog" aria-modal="true" aria-labelledby="delete-account-item-title">
        <div class="modal-head">
          <div>
            <h2 id="delete-account-item-title">{{ deleteTarget.needsCheck ? '核对删除结果' : (deleteTarget.type === 'profile' ? '确认删除儿童档案？' : '确认删除阅读记录？') }}</h2>
            <p>{{ deleteTarget.title }}{{ deleteTarget.type === 'profile' ? '关联的阅读会话、语音媒体和儿童授权也会一起删除。' : '删除后无法恢复。' }}</p>
          </div>
          <button class="modal-close" type="button" aria-label="关闭" :disabled="deleteBusy" @click="deleteTarget = null">×</button>
        </div>
        <p v-if="deleteError" class="form-error" role="alert">{{ deleteError }}</p>
        <div class="modal-actions">
          <button class="secondary" type="button" :disabled="deleteBusy" @click="deleteTarget = null">{{ deleteTarget.needsCheck ? '关闭' : '保留' }}</button>
          <button v-if="deleteTarget.needsCheck" class="primary" type="button" :disabled="deleteBusy" @click="confirmDelete">{{ deleteBusy ? '正在核对…' : '重新核对删除结果' }}</button>
          <button v-else class="danger" type="button" :disabled="deleteBusy" @click="confirmDelete">{{ deleteBusy ? '正在删除…' : '确认删除' }}</button>
        </div>
      </section>
    </div>
  </main>
</template>
