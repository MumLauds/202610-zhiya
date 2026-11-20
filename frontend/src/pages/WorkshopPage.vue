<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { mediaUrl } from '../api/client'

const props = defineProps({
  workshop: { type: Object, required: true },
  auth: { type: Object, required: true },
  activeProfile: { type: Object, default: null },
  sourceSessionId: { type: Number, default: null },
  generateAction: { type: Function, required: true },
  retryGenerationAction: { type: Function, required: true },
  resetAction: { type: Function, required: true },
  approveAction: { type: Function, required: true },
  openShelfAction: { type: Function, required: true },
  continueReadingAction: { type: Function, required: true },
})

const emit = defineEmits(['start-story', 'navigate'])

const ageOptions = [
  { value: 'AGE_3_4', label: '3–4 岁', note: '短句和重复节奏' },
  { value: 'AGE_4_5', label: '4–5 岁', note: '清楚的小冲突' },
  { value: 'AGE_5_6', label: '5–6 岁', note: '更完整的因果' },
]
const topicOptions = ['友谊', '勇气', '自然观察', '亲子依恋', '想象力', '生命成长']
const characterOptions = ['小兔', '小熊', '家人', '小狐狸', '小猫']
const sceneOptions = ['森林', '海边', '夜晚', '雨后花园', '山谷']

function initialForm() {
  return {
    ageBand: props.activeProfile?.ageBand || 'AGE_4_5',
    topic: '友谊',
    characters: ['小兔'],
    scene: '森林',
    branching: true,
    generateIllustrations: false,
  }
}

const form = reactive(initialForm())
const customCharacter = ref('')
const localError = ref('')
const reviewedAll = ref(false)

const continuation = computed(() => props.workshop.continuation)
const isContinuation = computed(() => Boolean(continuation.value))
const draft = computed(() => props.workshop.draft)
const approvedVersion = computed(() => props.workshop.approvedVersion)
const generationBusy = computed(() => ['running', 'checking'].includes(props.workshop.generationStatus))
const generationUnknown = computed(() => props.workshop.generationStatus === 'unknown')
const generationEnded = computed(() => props.workshop.generationStatus === 'endedUnknown')
const reviewVisible = computed(() => Boolean(draft.value) && ['review', 'approved'].includes(props.workshop.generationStatus))
const formLocked = computed(() => generationBusy.value || generationUnknown.value || generationEnded.value || reviewVisible.value)
const submitLabel = computed(() => {
  if (generationBusy.value) return '正在生成故事草稿…'
  if (generationEnded.value) return '本次生成已结束'
  if (approvedVersion.value) return '本书已审核'
  if (reviewVisible.value) return '请先审核当前草稿'
  if (generationUnknown.value) return '确认生成结果'
  return isContinuation.value ? '生成续写草稿' : '生成原创草稿'
})
const continuationPageCount = computed(() => continuation.value?.session?.visitedPageNumbers?.length || 0)
const continuationCanBranch = computed(() => !isContinuation.value || continuationPageCount.value <= 3)
const continuationCanGenerate = computed(() => !isContinuation.value || continuationPageCount.value <= 5)
const resumePageNumber = computed(() => continuation.value?.resumePageNumber || 1)
const draftCover = computed(() => draft.value?.coverRef || draft.value?.pages?.find((page) => page.imageRef)?.imageRef)
const pageByNumber = computed(() => new Map((draft.value?.pages || []).map((page) => [page.pageNumber, page])))
const branchesByPage = computed(() => {
  const groups = new Map()
  for (const branch of draft.value?.branches || []) {
    const items = groups.get(branch.fromPageNumber) || []
    items.push(branch)
    groups.set(branch.fromPageNumber, items)
  }
  return [...groups.entries()].map(([pageNumber, branches]) => ({ pageNumber, branches }))
})
const checks = computed(() => {
  const checks = draft.value?.checkResults || {}
  return [
    { key: 'structurePassed', label: '故事结构' },
    { key: 'safetyPassed', label: '内容安全' },
    { key: 'licensePassed', label: '配图许可' },
  ].map((item) => ({ ...item, passed: checks[item.key] === true }))
})
const allChecksPassed = computed(() => checks.value.every((item) => item.passed))
const selectedAge = computed(() => {
  const ageBand = isContinuation.value ? continuation.value.profile?.ageBand : form.ageBand
  return ageOptions.find(item => item.value === ageBand)?.label || '适龄范围'
})
const pageLabel = computed(() => draft.value ? `${draft.value.pages?.length || 0} 页` : '4–6 页')
const editStatusLabel = computed(() => {
  if (generationBusy.value) return '生成中'
  if (props.workshop.generationStatus === 'review') return '待审核'
  if (props.workshop.generationStatus === 'approved') return '已审核'
  return '可编辑'
})

watch(() => props.activeProfile?.ageBand, (ageBand) => {
  if (!isContinuation.value && ['AGE_3_4', 'AGE_4_5', 'AGE_5_6'].includes(ageBand)) form.ageBand = ageBand
})

watch(() => props.workshop.draft?.storyVersionId, () => {
  reviewedAll.value = false
})

watch(() => continuationPageCount.value, (count) => {
  if (isContinuation.value && count > 3) form.branching = false
}, { immediate: true })

function toggleCharacter(character) {
  localError.value = ''
  if (form.characters.includes(character)) {
    if (form.characters.length === 1) {
      localError.value = '至少保留一个故事角色。'
      return
    }
    form.characters = form.characters.filter((item) => item !== character)
    return
  }
  if (form.characters.length >= 8) {
    localError.value = '最多同时使用 8 个角色。'
    return
  }
  form.characters = [...form.characters, character]
}

function addCharacter() {
  const value = customCharacter.value.trim()
  if (!value) return
  if (form.characters.includes(value)) {
    customCharacter.value = ''
    return
  }
  if (form.characters.length >= 8) {
    localError.value = '最多同时使用 8 个角色。'
    return
  }
  form.characters = [...form.characters, value]
  customCharacter.value = ''
  localError.value = ''
}

function submitGeneration() {
  localError.value = ''
  if (!form.topic || !form.scene || !form.characters.length) {
    localError.value = '请完整选择主题、角色和场景。'
    return
  }
  if (!continuationCanGenerate.value) {
    localError.value = '已读页数已达到 6 页上限，不能继续生成。'
    return
  }
  return props.generateAction({
    ...form,
    characters: [...form.characters],
    branching: continuationCanBranch.value && form.branching,
  })
}

function resetDraft() {
  const newStory = Boolean(approvedVersion.value)
  if (!props.resetAction()) return
  if (newStory) {
    Object.assign(form, initialForm())
    customCharacter.value = ''
  }
  reviewedAll.value = false
  localError.value = ''
}

function openShelf() {
  props.openShelfAction()
}

function startApprovedStory() {
  if (!approvedVersion.value) return
  emit('start-story', {
    ...approvedVersion.value,
    ageBand: form.ageBand || props.activeProfile?.ageBand || '',
    topics: draft.value?.topics || [form.topic],
  })
}

function pageImage(page) {
  if (page?.imageUrl) return page.imageUrl
  return mediaUrl(page?.imageRef, 'reading') || '/assets/image-placeholder.svg'
}

function branchTarget(branch) {
  return pageByNumber.value.get(branch.nextPageNumber)?.text || `第 ${branch.nextPageNumber} 页`
}
</script>

<template>
  <main class="workshop-page page-width">
    <header class="workshop-heading">
      <div class="workshop-heading-copy">
        <p class="eyebrow">{{ isContinuation ? '从当前阅读继续' : '原创故事工坊' }}</p>
        <h1>{{ isContinuation ? '续写这次共读的故事' : '为孩子创作一本原创绘本' }}</h1>
        <p v-if="isContinuation">
          保留已经读过的正文和配图，再从故事停下的地方接上新内容。审核通过后才会回到亲子阅读。
        </p>
        <p v-else>
          设置适读年龄、主题、角色和场景，生成 4–6 页个性化故事草稿。逐页核对正文、配图和剧情分支后，才会进入私人书架。
        </p>
        <div class="workshop-heading-actions">
          <button class="secondary" type="button" @click="emit('navigate', 'library')">回到绘本馆</button>
        </div>
      </div>
      <aside class="workshop-rule-card" aria-label="原创故事可见范围">
        <span class="rule-mark">私</span>
        <div>
          <strong>草稿只对你可见</strong>
          <p>未审核故事不会公开，也不能进入儿童模式。审核通过后是仅属于你的私人绘本。</p>
        </div>
        <ol>
          <li><span>1</span>设置故事条件</li>
          <li><span>2</span>家长逐页审核</li>
          <li><span>3</span>阅读或授权儿童模式</li>
        </ol>
      </aside>
    </header>

    <div class="workshop-workspace">
      <aside id="workshop-conditions" class="workshop-form-card" tabindex="-1">
        <div class="workshop-card-heading">
          <div>
            <span>创作条件</span>
            <h2>{{ isContinuation ? `《${continuation?.version?.title || '当前故事'}》续写` : '这次想讲什么？' }}</h2>
          </div>
          <span class="status-dot" :class="{ busy: generationBusy }">{{ editStatusLabel }}</span>
        </div>

        <div
          v-if="sourceSessionId && !props.workshop.continuationError && (props.workshop.continuationLoading || !continuation)"
          class="continuation-loading"
          role="status"
        >
          正在读取当前阅读进度与前文配图…
        </div>
        <div v-else-if="props.workshop.continuationError" class="continuation-error" role="alert">
          <strong>暂时不能续写</strong>
          <p>{{ props.workshop.continuationError }}</p>
          <button class="secondary" type="button" @click="emit('navigate', 'library')">返回绘本馆</button>
        </div>

        <template v-else>
          <section v-if="isContinuation" class="continuation-card" aria-label="续写前文">
            <div class="continuation-card-art" aria-hidden="true">
              <span>{{ continuationPageCount }}</span>
              <small>页已读记录</small>
            </div>
            <div>
              <h3>前文会原样保留</h3>
              <p>本次续写使用源会话档案：{{ continuation.profile?.nickname || `档案 #${continuation.session.childProfileId}` }}。</p>
              <p>正文与已审核配图固定不变，新内容从第 {{ resumePageNumber }} 页接入。</p>
              <span class="branch-note" :class="{ warning: !continuationCanBranch }">
                {{ continuationCanBranch ? '这次仍可选择是否加入剧情分支' : '前文页数较多，续写将使用单线故事' }}
              </span>
            </div>
          </section>

          <form class="workshop-form" :class="{ locked: formLocked }" @submit.prevent="submitGeneration">
            <fieldset v-if="!isContinuation" class="workshop-fieldset">
              <legend><span>01</span>适读年龄</legend>
              <div class="age-options">
                <label v-for="age in ageOptions" :key="age.value" :class="{ selected: form.ageBand === age.value }">
                  <input v-model="form.ageBand" type="radio" name="workshop-age" :value="age.value" :disabled="formLocked">
                  <strong>{{ age.label }}</strong>
                  <small>{{ age.note }}</small>
                </label>
              </div>
            </fieldset>

            <fieldset class="workshop-fieldset">
              <legend><span>{{ isContinuation ? '01' : '02' }}</span>故事主题</legend>
              <div class="choice-row">
                <button
                  v-for="topic in topicOptions"
                  :key="topic"
                  class="choice-chip"
                  :class="{ selected: form.topic === topic }"
                  type="button"
                  :disabled="formLocked"
                  @click="form.topic = topic"
                >{{ topic }}</button>
              </div>
            </fieldset>

            <fieldset class="workshop-fieldset">
              <legend><span>{{ isContinuation ? '02' : '03' }}</span>故事角色</legend>
              <div class="choice-row">
                <button
                  v-for="character in characterOptions"
                  :key="character"
                  class="choice-chip"
                  :class="{ selected: form.characters.includes(character) }"
                  type="button"
                  :disabled="formLocked"
                  @click="toggleCharacter(character)"
                >{{ character }}</button>
              </div>
              <div class="custom-character">
                <label for="workshop-character">添加自定义角色</label>
                <div>
                  <input
                    id="workshop-character"
                    v-model="customCharacter"
                    maxlength="120"
                    placeholder="例如：戴黄帽子的小鸭"
                    :disabled="formLocked"
                    @keydown.enter.prevent="addCharacter"
                  >
                  <button class="secondary" type="button" :disabled="formLocked" @click="addCharacter">加入角色</button>
                </div>
              </div>
            </fieldset>

            <fieldset class="workshop-fieldset">
              <legend><span>{{ isContinuation ? '03' : '04' }}</span>故事场景</legend>
              <div class="choice-row">
                <button
                  v-for="scene in sceneOptions"
                  :key="scene"
                  class="choice-chip"
                  :class="{ selected: form.scene === scene }"
                  type="button"
                  :disabled="formLocked"
                  @click="form.scene = scene"
                >{{ scene }}</button>
              </div>
            </fieldset>

            <fieldset class="workshop-fieldset">
              <legend><span>{{ isContinuation ? '04' : '05' }}</span>剧情分支</legend>
              <label class="branch-switch" :class="{ disabled: !continuationCanBranch }">
                <input v-model="form.branching" type="checkbox" :disabled="formLocked || !continuationCanBranch">
                <span class="switch-track"><span /></span>
                <span>
                  <strong>加入两个剧情选择</strong>
                  <small>{{ continuationCanBranch ? '孩子可以选一条路线，两条路线都会回到完整结局。' : '当前前文接近整本页数上限，这次保持单线。' }}</small>
                </span>
              </label>
            </fieldset>

            <p v-if="localError || props.workshop.generationError" class="workshop-error" role="alert">
              {{ localError || props.workshop.generationError }}
            </p>
            <p v-if="!continuationCanGenerate" class="workshop-error" role="alert">
              已读内容已达到 6 页上限，当前会话不能继续续写。
            </p>

            <label class="branch-switch">
              <input v-model="form.generateIllustrations" type="checkbox" :disabled="formLocked">
              <span class="switch-track"><span /></span>
              <span>
                <strong>使用 AI 生成专属插画</strong>
                <small>{{ isContinuation ? '只为续写的新页面生成。' : '按每页正文生成。' }} 需要已配置的生图服务，耗时较长。</small>
              </span>
            </label>
            <button
              class="primary workshop-submit"
              type="submit"
              :disabled="generationBusy || generationEnded || reviewVisible || !continuationCanGenerate"
            >
              {{ submitLabel }}
            </button>
            <p class="workshop-form-note">
              {{ form.generateIllustrations ? '文字和 AI 插画完成后，请逐页核对画面。' : '默认使用已经审核的模板插图。' }}
            </p>
          </form>
        </template>
      </aside>

      <section class="workshop-stage" aria-live="polite">
        <div v-if="generationBusy" class="workshop-state workshop-loading-state">
          <div class="story-spinner" aria-hidden="true"><span /><span /><span /></div>
          <p class="eyebrow">正在组织故事</p>
          <h2>{{ props.workshop.generationStatus === 'checking' ? '正在确认原生成请求' : '让文字、配图和分支落到每一页' }}</h2>
          <p>{{ selectedAge }} · {{ form.topic }} · {{ form.characters.join('、') }} · {{ form.scene }}</p>
          <div class="draft-skeleton" aria-hidden="true">
            <span /><span /><span />
          </div>
          <small>生成请求最长约 60 秒；页面会保留这一次请求，不会自动重复提交。</small>
        </div>

        <div v-else-if="generationUnknown || generationEnded" class="workshop-state workshop-result-state">
          <span class="state-symbol">?</span>
          <p class="eyebrow">{{ generationEnded ? '生成请求已结束' : '生成结果待确认' }}</p>
          <h2>{{ generationEnded ? '无法确认本次生成结果' : '这次生成可能仍在处理中' }}</h2>
          <p>{{ props.workshop.generationError }}</p>
          <button v-if="generationEnded" class="primary" type="button" @click="resetDraft">结束本次创作</button>
          <button v-else class="primary" type="button" @click="props.retryGenerationAction()">确认生成结果</button>
        </div>

        <div v-else-if="props.workshop.generationStatus === 'failed'" class="workshop-state workshop-result-state error-state">
          <span class="state-symbol">!</span>
          <p class="eyebrow">这次没有生成草稿</p>
          <h2>故事暂时没有写出来</h2>
          <p>{{ props.workshop.generationError || '服务暂时不可用，请稍后再试。' }}</p>
          <button class="primary" type="button" @click="resetDraft">调整条件后重试</button>
        </div>

        <article v-else-if="reviewVisible" class="draft-review">
          <header class="draft-heading">
            <div class="draft-heading-book">
              <img v-if="draftCover" class="draft-cover" :src="mediaUrl(draftCover, 'thumbnail')" :alt="`${draft.title}封面`">
              <div>
                <span class="pill" :class="{ warm: props.workshop.generationStatus === 'approved' }">
                  {{ props.workshop.generationStatus === 'approved' ? '已审核私人绘本' : '待家长审核' }}
                </span>
                <h2>{{ draft.title }}</h2>
                <p>第 {{ draft.versionNumber }} 版 · {{ pageLabel }} · {{ selectedAge }}</p>
              </div>
            </div>
            <div class="draft-heading-actions">
              <span v-if="isContinuation" class="draft-continuation-badge">从第 {{ resumePageNumber }} 页续写</span>
              <button v-if="!approvedVersion || !isContinuation" class="text-button" type="button" :disabled="props.workshop.approvalBusy" @click="resetDraft">
                {{ approvedVersion ? '再创作一本' : '重新设置条件' }}
              </button>
            </div>
          </header>

          <div v-if="isContinuation" class="carried-prefix-note">
            <strong>前文已保留</strong>
            前 {{ continuationPageCount }} 个已读页面沿用原文和冻结配图；从第 {{ resumePageNumber }} 页开始是新增内容。
          </div>

          <div v-if="props.workshop.generationStatus === 'review'" class="check-strip" aria-label="内容检查结果">
            <span v-for="check in checks" :key="check.key" :class="{ passed: check.passed }">
              <b>{{ check.passed ? '✓' : '!' }}</b>{{ check.label }}{{ check.passed ? '通过' : '待确认' }}
            </span>
          </div>

          <div class="review-pages">
            <article v-for="page in draft.pages" :key="page.pageNumber" class="review-page">
              <figure>
                <img loading="lazy" :src="pageImage(page)" :alt="`第 ${page.pageNumber} 页${page.imageSource === 'AI_GENERATED' ? ' AI 插画' : '配图'}`">
                <span v-if="isContinuation && page.pageNumber < resumePageNumber" class="page-origin">保留前文</span>
                <span v-else-if="isContinuation" class="page-origin new">续写内容</span>
              </figure>
              <div class="review-page-copy">
                <span>第 {{ page.pageNumber }} 页<span v-if="page.imageSource === 'AI_GENERATED'"> · AI 插画</span></span>
                <p>{{ page.text }}</p>
              </div>
            </article>
          </div>

          <section v-if="branchesByPage.length" class="branch-review" aria-labelledby="branch-review-title">
            <div class="branch-review-heading">
              <div>
                <p class="eyebrow">剧情分支</p>
                <h3 id="branch-review-title">从第 {{ branchesByPage[0].pageNumber }} 页开始，孩子可以选两条路</h3>
              </div>
              <span>两条路线都通向完整结局</span>
            </div>
            <div v-for="group in branchesByPage" :key="group.pageNumber" class="branch-group">
              <div v-for="branch in group.branches" :key="branch.branchId" class="branch-option">
                <span class="branch-label">{{ branch.choiceLabel }}</span>
                <strong>进入第 {{ branch.nextPageNumber }} 页</strong>
                <p>{{ branchTarget(branch) }}</p>
                <small>{{ branch.reviewStatus === 'PENDING' ? '等待家长审核' : '已审核' }}</small>
              </div>
            </div>
          </section>

          <section v-if="props.workshop.generationStatus === 'review'" class="approval-panel">
            <div>
              <p class="eyebrow">家长审核</p>
              <h3>确认后才会进入私人书架</h3>
              <p>请核对全部 {{ draft.pages.length }} 页正文、配图和剧情分支。审核通过前，儿童模式无法读取这本故事。</p>
            </div>
            <label class="approval-check">
              <input v-model="reviewedAll" type="checkbox" :disabled="props.workshop.approvalBusy">
              <span>我已逐页查看正文、配图和全部剧情分支</span>
            </label>
            <p v-if="props.workshop.generationError" class="workshop-error" role="alert">{{ props.workshop.generationError }}</p>
            <button
              class="primary"
              type="button"
              :disabled="!reviewedAll || !allChecksPassed || props.workshop.approvalBusy"
              @click="props.approveAction()"
            >
              {{ props.workshop.approvalBusy ? '正在审核并保存…' : '审核通过并加入私人书架' }}
            </button>
          </section>

          <section v-else class="approved-panel">
            <span class="approved-symbol" aria-hidden="true">✓</span>
            <div>
              <p class="eyebrow">家庭专属绘本</p>
              <h2>故事已经可以阅读了</h2>
              <p v-if="draft.continuation">
                已审核版本保留前 {{ continuationPageCount }} 个阅读页面，并从第 {{ draft.continuation.resumePageNumber }} 页继续。
              </p>
              <p v-else>
                《{{ draft.title }}》已经作为 <strong>APPROVED_PRIVATE</strong> 私人绘本进入你的书架，可以开始亲子阅读或创建儿童阅读会话。
              </p>
            </div>
            <div class="approved-actions">
              <button
                v-if="draft.continuation"
                class="primary"
                type="button"
                :disabled="props.workshop.switchBusy"
                @click="props.continueReadingAction()"
              >
                {{ props.workshop.switchBusy ? '正在切换到续写阅读…' : `从第 ${draft.continuation.resumePageNumber} 页继续阅读` }}
              </button>
              <button v-else class="primary" type="button" @click="startApprovedStory">开始阅读这本绘本</button>
              <button class="secondary" type="button" :disabled="props.workshop.shelfBusy" @click="openShelf">
                {{ props.workshop.shelfBusy ? '正在读取书架…' : props.workshop.shelfError ? '重试读取私人书架' : '查看私人书架' }}
              </button>
            </div>
            <p v-if="props.workshop.shelfError" class="workshop-error" role="alert">{{ props.workshop.shelfError }}</p>
            <p v-if="props.workshop.switchError" class="workshop-error" role="alert">{{ props.workshop.switchError }}</p>
          </section>
        </article>

        <div v-else class="workshop-blank">
          <div class="blank-book-scene" aria-hidden="true">
            <div class="paper-sheet back"><span /></div>
            <div class="paper-sheet middle"><span /></div>
            <div class="paper-sheet front">
              <b>✧</b>
              <i />
              <i />
              <i />
            </div>
            <span class="scene-leaf leaf-one" />
            <span class="scene-leaf leaf-two" />
          </div>
          <p class="eyebrow">你的故事空白页</p>
          <h2>设置一个孩子熟悉的世界</h2>
          <p>这里会展示生成的标题、4–6 页正文、配图、剧情选项与内容检查结果。只有你确认后，故事才会进入阅读。</p>
          <div class="blank-facts">
            <span><strong>{{ selectedAge }}</strong>适龄故事</span>
            <span><strong>{{ pageLabel }}</strong>完整结构</span>
            <span><strong>{{ form.topic }}</strong>故事主题</span>
          </div>
        </div>
      </section>
    </div>
  </main>
</template>

<style scoped src="./WorkshopPage.css"></style>
