<script setup>
import { computed, reactive, ref } from 'vue'

const props = defineProps({
  section: { type: String, default: 'storybooks' },
  admin: { type: Object, required: true },
  createStoryAction: { type: Function, required: true },
  setStoryStatusAction: { type: Function, required: true },
  createKnowledgeAction: { type: Function, required: true },
  updateKnowledgeAction: { type: Function, required: true },
})

const emit = defineEmits(['navigate', 'sign-out'])
const storyForm = reactive({
  title: '',
  ageBand: 'AGE_4_5',
  topics: '',
  licenseNote: '',
  pagesText: '',
  imageRefs: '',
})
const knowledgeForm = reactive({
  itemKey: '',
  title: '',
  sourceTitle: '',
  sourceReference: '',
  locator: '',
  ageBand: '',
  topicTags: '',
  taskTypes: 'GUIDANCE',
  content: '',
  status: 'DRAFT',
})
const revising = ref(null)
const revisionForm = reactive({ content: '', status: 'DRAFT' })
const localError = ref('')

const sectionError = computed(() => props.section === 'knowledge' ? props.admin.knowledgeError : props.admin.error)
const sectionLoading = computed(() => props.section === 'knowledge' ? props.admin.loading : props.admin.storiesLoading)
const currentItems = computed(() => props.section === 'knowledge'
  ? props.admin.knowledgeItems
  : props.admin.storyVersions)

function splitList(value) {
  return [...new Set(String(value).split(/[,，\n]/).map((item) => item.trim()).filter(Boolean))]
}

function splitPages(value) {
  return String(value).split(/\n\s*\n|\n/).map((item) => item.trim()).filter(Boolean)
}

async function createStory() {
  localError.value = ''
  const pages = splitPages(storyForm.pagesText)
  const imageRefs = storyForm.imageRefs.split(/\r?\n/).map((ref) => ref.trim())
  if (!storyForm.title.trim() || !storyForm.licenseNote.trim() || !pages.length) {
    localError.value = '请填写标题、许可依据和至少一页正文。'
    return
  }
  const payload = {
    title: storyForm.title.trim(),
    ageBand: storyForm.ageBand,
    topics: splitList(storyForm.topics),
    licenseNote: storyForm.licenseNote.trim(),
    pages: pages.map((text, index) => ({
      pageNumber: index + 1,
      text,
      imageRef: imageRefs[index] || 'none',
      defaultNextPageNumber: index + 1 < pages.length ? index + 2 : null,
    })),
    branches: [],
  }
  if (!payload.topics.length) {
    localError.value = '请至少填写一个主题。'
    return
  }
  if (await props.createStoryAction(payload)) {
    Object.assign(storyForm, { title: '', topics: '', licenseNote: '', pagesText: '', imageRefs: '' })
  }
}

async function createKnowledge() {
  localError.value = ''
  const payload = {
    itemKey: knowledgeForm.itemKey.trim(),
    title: knowledgeForm.title.trim(),
    sourceTitle: knowledgeForm.sourceTitle.trim(),
    sourceReference: knowledgeForm.sourceReference.trim(),
    locator: knowledgeForm.locator.trim(),
    ageBand: knowledgeForm.ageBand || null,
    topicTags: splitList(knowledgeForm.topicTags),
    taskTypes: splitList(knowledgeForm.taskTypes),
    content: knowledgeForm.content.trim(),
    status: knowledgeForm.status,
  }
  if (!payload.itemKey || !payload.title || !payload.sourceTitle || !payload.sourceReference
      || !payload.locator || !payload.topicTags.length || !payload.taskTypes.length || !payload.content) {
    localError.value = '请填写知识标识、标题、来源、标签和正文。'
    return
  }
  if (await props.createKnowledgeAction(payload)) {
    Object.assign(knowledgeForm, {
      itemKey: '', title: '', sourceTitle: '', sourceReference: '', locator: '',
      ageBand: '', topicTags: '', taskTypes: 'GUIDANCE', content: '', status: 'DRAFT',
    })
  }
}

function openRevision(item) {
  revising.value = item
  revisionForm.content = item.content
  revisionForm.status = item.status === 'APPROVED' ? 'APPROVED' : 'DRAFT'
  localError.value = ''
}

async function saveRevision() {
  if (!revising.value || !revisionForm.content.trim()) return
  const saved = await props.updateKnowledgeAction(revising.value.knowledgeItemId, {
    content: revisionForm.content.trim(),
    status: revisionForm.status,
  })
  if (saved) revising.value = null
}
</script>

<template>
  <main class="admin-page page-width">
    <header class="admin-heading">
      <div>
        <p class="eyebrow">内容管理员工作台</p>
        <h1>绘本与知识管理</h1>
        <p>创建草稿、审核发布并维护可检索的共读知识。所有发布操作都会记录管理员和审核时间。</p>
      </div>
      <button class="secondary" type="button" @click="emit('sign-out')">退出账号</button>
    </header>

    <nav class="admin-tabs" aria-label="内容管理分类">
      <button type="button" :class="{ active: section === 'storybooks' }" @click="emit('navigate', 'admin/storybooks')">绘本管理</button>
      <button type="button" :class="{ active: section === 'knowledge' }" @click="emit('navigate', 'admin/knowledge')">知识管理</button>
    </nav>

    <p v-if="sectionError || localError" class="form-error" role="alert">{{ sectionError || localError }}</p>
    <p v-if="sectionLoading" class="admin-loading" role="status">正在读取内容…</p>

    <template v-if="section === 'storybooks'">
      <section class="admin-toolbar">
        <div><strong>资料库绘本版本</strong><span>{{ currentItems.length }} 个版本</span></div>
        <details class="admin-create-panel">
          <summary class="primary">＋ 新建绘本草稿</summary>
          <form class="admin-form" @submit.prevent="createStory">
            <label class="form-field"><span>标题</span><input v-model="storyForm.title" maxlength="120" placeholder="例如：纸船停在哪里"></label>
            <label class="form-field"><span>适龄</span>
              <select v-model="storyForm.ageBand">
                <option value="AGE_3_4">3～4 岁</option>
                <option value="AGE_4_5">4～5 岁</option>
                <option value="AGE_5_6">5～6 岁</option>
              </select>
            </label>
            <label class="form-field"><span>主题标签</span><input v-model="storyForm.topics" placeholder="互助，自然观察"></label>
            <label class="form-field"><span>许可依据</span><input v-model="storyForm.licenseNote" maxlength="500" placeholder="自制或明确授权依据"></label>
            <label class="form-field admin-wide"><span>页面正文</span><textarea v-model="storyForm.pagesText" rows="7" placeholder="每行一页正文。默认下一页按顺序连接。"></textarea></label>
            <label class="form-field admin-wide"><span>图片资源键</span><textarea v-model="storyForm.imageRefs" rows="3" placeholder="每行对应一页；留空表示该页无配图。"></textarea></label>
            <button class="primary" type="submit" :disabled="sectionLoading || admin.busy">{{ admin.busy ? '正在创建…' : '创建草稿' }}</button>
          </form>
        </details>
      </section>

      <section class="admin-list">
        <article v-for="story in currentItems" :key="story.storyVersionId" class="admin-card">
          <div class="admin-card-head">
            <div><span class="pill">{{ story.status }}</span><h2>{{ story.title }}</h2></div>
            <small>版本 {{ story.versionNumber }} · ID {{ story.storyVersionId }}</small>
          </div>
          <p>{{ story.ageBand || '适龄待确认' }} · {{ story.pages.length }} 页 · {{ story.topics?.join(' / ') || '主题待确认' }}</p>
          <p v-if="story.licenseNote" class="admin-license">许可：{{ story.licenseNote }}</p>
          <p v-if="story.validationErrors?.length" class="admin-warning">{{ story.validationErrors.join('；') }}</p>
          <div class="admin-card-actions">
            <button v-if="story.status === 'DRAFT'" class="primary" type="button" :disabled="sectionLoading || admin.busy || story.canPublish === false" @click="setStoryStatusAction(story.storyVersionId, 'PUBLISHED')">审核并发布</button>
            <button v-if="story.status === 'PUBLISHED'" class="danger" type="button" :disabled="sectionLoading || admin.busy" @click="setStoryStatusAction(story.storyVersionId, 'WITHDRAWN')">撤回</button>
            <details>
              <summary class="secondary">查看页面</summary>
              <ol class="admin-pages">
                <li v-for="page in story.pages" :key="page.pageNumber"><strong>第 {{ page.pageNumber }} 页</strong><p>{{ page.text }}</p></li>
              </ol>
              <ul v-if="story.branches?.length"><li v-for="branch in story.branches" :key="branch.branchId">第 {{ branch.fromPageNumber }} 页 → 第 {{ branch.nextPageNumber }} 页：{{ branch.choiceLabel }}（{{ branch.reviewStatus }}）</li></ul>
            </details>
          </div>
        </article>
        <p v-if="!currentItems.length && !sectionLoading && !sectionError" class="admin-empty">还没有绘本版本。</p>
      </section>
    </template>

    <template v-else>
      <section class="admin-toolbar">
        <div><strong>知识版本</strong><span>{{ currentItems.length }} 个版本</span></div>
        <details class="admin-create-panel">
          <summary class="primary">＋ 新建知识条目</summary>
          <form class="admin-form" @submit.prevent="createKnowledge">
            <label class="form-field"><span>稳定标识</span><input v-model="knowledgeForm.itemKey" placeholder="guided-observation"></label>
            <label class="form-field"><span>标题</span><input v-model="knowledgeForm.title"></label>
            <label class="form-field"><span>来源名称</span><input v-model="knowledgeForm.sourceTitle"></label>
            <label class="form-field"><span>来源定位</span><input v-model="knowledgeForm.sourceReference"></label>
            <label class="form-field"><span>章节/定位</span><input v-model="knowledgeForm.locator"></label>
            <label class="form-field"><span>年龄段</span><select v-model="knowledgeForm.ageBand"><option value="">全龄</option><option value="AGE_3_4">3～4 岁</option><option value="AGE_4_5">4～5 岁</option><option value="AGE_5_6">5～6 岁</option></select></label>
            <label class="form-field"><span>主题标签</span><input v-model="knowledgeForm.topicTags" placeholder="通用，亲子阅读"></label>
            <label class="form-field"><span>任务类型</span><input v-model="knowledgeForm.taskTypes" placeholder="GUIDANCE, SAFETY"></label>
            <label class="form-field"><span>初始状态</span><select v-model="knowledgeForm.status"><option value="DRAFT">草稿</option><option value="APPROVED">审核通过</option></select></label>
            <label class="form-field admin-wide"><span>知识正文</span><textarea v-model="knowledgeForm.content" rows="6"></textarea></label>
            <button class="primary" type="submit" :disabled="sectionLoading || admin.busy">{{ admin.busy ? '正在保存…' : '创建知识版本' }}</button>
          </form>
        </details>
      </section>

      <section class="admin-list">
        <article v-for="item in currentItems" :key="item.knowledgeItemId" class="admin-card">
          <div class="admin-card-head">
            <div><span class="pill">{{ item.status }}</span><h2>{{ item.title }}</h2></div>
            <small>{{ item.itemKey }} · v{{ item.versionNumber }}</small>
          </div>
          <p>{{ item.sourceTitle }} · {{ item.ageBand || '全龄' }}</p>
          <p>来源定位：{{ item.sourceReference }}<span v-if="item.locator"> · {{ item.locator }}</span></p>
          <p class="admin-content">{{ item.content }}</p>
          <div class="admin-card-actions">
            <button v-if="item.status === 'DRAFT'" class="primary" type="button" :disabled="sectionLoading || admin.busy" @click="updateKnowledgeAction(item.knowledgeItemId, { status: 'APPROVED' })">审核通过</button>
            <button v-if="item.status !== 'RETIRED'" class="secondary" type="button" :disabled="sectionLoading || admin.busy" @click="openRevision(item)">创建修订</button>
            <button v-if="item.status === 'APPROVED'" class="danger" type="button" :disabled="sectionLoading || admin.busy" @click="updateKnowledgeAction(item.knowledgeItemId, { status: 'RETIRED' })">停用</button>
          </div>
        </article>
        <p v-if="!currentItems.length && !sectionLoading && !sectionError" class="admin-empty">还没有知识条目。</p>
      </section>
    </template>

    <div v-if="revising" class="modal-backdrop" @click.self="revising = null">
      <section class="modal-card" role="dialog" aria-modal="true" aria-labelledby="knowledge-revision-title">
        <div class="modal-head">
          <div><h2 id="knowledge-revision-title">创建知识修订</h2><p>{{ revising.itemKey }} · 将生成新版本并保留历史引用</p></div>
          <button class="modal-close" type="button" aria-label="关闭" @click="revising = null">×</button>
        </div>
        <label class="form-field"><span>修订正文</span><textarea v-model="revisionForm.content" rows="7"></textarea></label>
        <label class="form-field"><span>新版本状态</span><select v-model="revisionForm.status"><option value="DRAFT">草稿</option><option value="APPROVED">审核通过</option></select></label>
        <div class="modal-actions">
          <button class="secondary" type="button" :disabled="sectionLoading || admin.busy" @click="revising = null">取消</button>
          <button class="primary" type="button" :disabled="sectionLoading || admin.busy || !revisionForm.content.trim()" @click="saveRevision">{{ admin.busy ? '正在保存…' : '保存新版本' }}</button>
        </div>
      </section>
    </div>
  </main>
</template>
