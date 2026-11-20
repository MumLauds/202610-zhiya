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
const firstIllustration = computed(() => props.workshop.draft?.pages?.find((page) => page.imageRef)?.imageRef || '')
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
  return mediaUrl(page?.imageRef) || '/assets/image-placeholder.svg'
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
          设置适读年龄、主题、角色和场景，生成 4–6 页个性化故事草稿。逐页核对正文、模板配图和剧情分支后，才会进入私人书架。
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

            <button
              class="primary workshop-submit"
              type="submit"
              :disabled="generationBusy || generationEnded || reviewVisible || !continuationCanGenerate"
            >
              {{ submitLabel }}
            </button>
            <p class="workshop-form-note">
              生成通常需要一段时间。插图来自已经审核的模板，不会实时生成专属图片。
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
            <div>
              <span class="pill" :class="{ warm: props.workshop.generationStatus === 'approved' }">
                {{ props.workshop.generationStatus === 'approved' ? '已审核私人绘本' : '待家长审核' }}
              </span>
              <h2>{{ draft.title }}</h2>
              <p>第 {{ draft.versionNumber }} 版 · {{ pageLabel }} · {{ selectedAge }}</p>
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
                <img :src="pageImage(page)" :alt="`第 ${page.pageNumber} 页模板配图`">
                <span v-if="isContinuation && page.pageNumber < resumePageNumber" class="page-origin">保留前文</span>
                <span v-else-if="isContinuation" class="page-origin new">续写内容</span>
              </figure>
              <div class="review-page-copy">
                <span>第 {{ page.pageNumber }} 页</span>
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
              <p>请核对全部 {{ draft.pages.length }} 页正文、模板配图和剧情分支。审核通过前，儿童模式无法读取这本故事。</p>
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
          <p>这里会展示生成的标题、4–6 页正文、模板配图、剧情选项与内容检查结果。只有你确认后，故事才会进入阅读。</p>
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

<style scoped>
.workshop-page {
  padding: 32px 0 64px;
  color: var(--ink);
}

.workshop-heading {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(280px, .62fr);
  gap: 24px;
  overflow: hidden;
  padding: 26px 32px;
  border: 1px solid rgba(226, 211, 188, .86);
  border-radius: 28px;
  background:
    radial-gradient(circle at 87% 20%, rgba(241, 203, 130, .3), transparent 18rem),
    linear-gradient(135deg, rgba(255, 251, 243, .98), rgba(239, 245, 235, .94));
  box-shadow: var(--shadow-md);
}

.workshop-heading::after {
  content: "";
  position: absolute;
  width: 240px;
  height: 240px;
  right: -90px;
  bottom: -160px;
  border: 1px solid rgba(70, 107, 81, .12);
  border-radius: 50%;
  box-shadow: 0 0 0 36px rgba(70, 107, 81, .035), 0 0 0 72px rgba(70, 107, 81, .025);
}

.workshop-heading-copy,
.workshop-rule-card {
  position: relative;
  z-index: 1;
}

.workshop-heading h1 {
  max-width: 760px;
  margin: 0;
  font-family: var(--font-story);
  font-size: clamp(27px, 2.2vw, 33px);
  font-weight: 500;
  line-height: 1.1;
  letter-spacing: -.025em;
}

.workshop-heading-copy > p:not(.eyebrow) {
  max-width: 720px;
  margin: 14px 0 0;
  color: var(--ink-soft);
  font-size: 14px;
  line-height: 24px;
}

.workshop-heading-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 18px;
}

.workshop-rule-card {
  align-self: stretch;
  display: grid;
  align-content: start;
  grid-template-columns: 40px minmax(0, 1fr);
  gap: 11px;
  padding: 18px;
  border: 1px solid rgba(70, 107, 81, .14);
  border-radius: 20px;
  background: rgba(255, 253, 248, .76);
  box-shadow: 0 12px 30px rgba(55, 71, 57, .08);
}

.rule-mark {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border-radius: 15px;
  color: var(--cream);
  background: var(--leaf);
  font-family: var(--font-story);
  font-size: 20px;
}

.workshop-rule-card strong {
  display: block;
  font-family: var(--font-story);
  font-size: 17px;
  font-weight: 600;
  line-height: 24px;
}

.workshop-rule-card p {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 19px;
}

.workshop-rule-card ol {
  grid-column: 1 / -1;
  display: grid;
  gap: 7px;
  margin: 4px 0 0;
  padding: 13px 0 0;
  border-top: 1px solid var(--line);
  list-style: none;
}

.workshop-rule-card li {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--ink-soft);
  font-size: 13px;
}

.workshop-rule-card li span {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: var(--brown);
  background: var(--sand);
  font-size: 11px;
  font-weight: 700;
}

.workshop-workspace {
  display: grid;
  grid-template-columns: minmax(320px, 385px) minmax(0, 1fr);
  gap: 20px;
  align-items: start;
  margin-top: 20px;
}

.workshop-form-card,
.workshop-stage {
  border: 1px solid rgba(228, 216, 199, .92);
  background: rgba(255, 253, 248, .96);
  box-shadow: var(--shadow-sm);
}

.workshop-form-card {
  padding: 24px;
  border-radius: 24px;
}

.workshop-card-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--line);
}

.workshop-card-heading > div > span {
  color: var(--brown-soft);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: .08em;
}

.workshop-card-heading h2 {
  margin: 6px 0 0;
  font-family: var(--font-story);
  font-size: 25px;
  font-weight: 600;
  line-height: 34px;
}

.status-dot {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 9px;
  border-radius: 999px;
  color: var(--leaf);
  background: var(--leaf-pale);
  font-size: 11px;
}

.status-dot::before {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.status-dot.busy::before {
  animation: workshop-pulse 1.4s ease infinite;
}

.continuation-loading,
.continuation-error {
  margin-top: 18px;
  padding: 18px;
  border-radius: 16px;
  background: var(--leaf-wash);
  color: var(--ink-soft);
  font-size: 13px;
  line-height: 22px;
}

.continuation-error {
  background: var(--danger-soft);
}

.continuation-error strong {
  color: var(--danger);
}

.continuation-error p {
  margin: 5px 0 14px;
}

.continuation-card {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr);
  gap: 14px;
  margin-top: 18px;
  padding: 16px;
  border-radius: 18px;
  background: linear-gradient(145deg, #f7eddd, #eef4e9);
}

.continuation-card-art {
  min-height: 88px;
  display: grid;
  place-content: center;
  border-radius: 14px;
  color: var(--cream);
  background: var(--leaf);
  text-align: center;
}

.continuation-card-art span {
  font-family: var(--font-story);
  font-size: 32px;
  line-height: 1;
}

.continuation-card-art small {
  margin-top: 4px;
  font-size: 10px;
}

.continuation-card h3 {
  margin: 0;
  font-family: var(--font-story);
  font-size: 17px;
  line-height: 24px;
}

.continuation-card p {
  margin: 5px 0 8px;
  color: var(--muted);
  font-size: 12px;
  line-height: 20px;
}

.branch-note {
  color: var(--leaf);
  font-size: 11px;
  font-weight: 600;
}

.branch-note.warning {
  color: var(--brown);
}

.workshop-form {
  display: grid;
  gap: 21px;
  padding-top: 20px;
}

.workshop-form.locked .workshop-fieldset,
.workshop-form.locked .custom-character {
  opacity: .62;
}

.workshop-fieldset {
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
}

.workshop-fieldset legend {
  width: 100%;
  margin-bottom: 10px;
  color: var(--ink);
  font-size: 13px;
  font-weight: 700;
}

.workshop-fieldset legend span {
  margin-right: 7px;
  color: var(--brown-soft);
  font-size: 10px;
  letter-spacing: .04em;
}

.age-options {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 7px;
}

.age-options label {
  min-height: 78px;
  display: grid;
  align-content: center;
  gap: 4px;
  padding: 10px 8px;
  border: 1px solid var(--line);
  border-radius: 13px;
  background: #fffaf2;
  text-align: center;
  cursor: pointer;
  transition: transform .16s ease, border-color .16s ease, background-color .16s ease;
}

.age-options label:hover {
  transform: translateY(-1px);
  border-color: #cfb994;
}

.age-options label.selected {
  border-color: var(--leaf);
  background: var(--leaf-pale);
}

.age-options label:has(input:focus-visible),
.branch-switch:has(input:focus-visible) .switch-track {
  outline: 3px solid rgba(241, 203, 130, .85);
  outline-offset: 2px;
}

.age-options input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.age-options strong {
  font-family: var(--font-story);
  font-size: 15px;
  font-weight: 600;
}

.age-options small {
  color: var(--muted);
  font-size: 10px;
  line-height: 15px;
}

.choice-row {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.choice-chip {
  min-height: 34px;
  padding: 0 13px;
  border: 1px solid #dfd2c0;
  border-radius: 999px;
  color: var(--brown);
  background: #fffaf2;
  font-size: 12px;
  transition: transform .16s ease, color .16s ease, border-color .16s ease, background-color .16s ease;
}

.choice-chip:hover {
  transform: translateY(-1px);
  border-color: #c9b38f;
  background: #fff4e4;
}

.choice-chip.selected {
  color: #fff;
  border-color: var(--leaf);
  background: var(--leaf);
}

.custom-character {
  display: grid;
  gap: 7px;
  margin-top: 11px;
}

.custom-character > label {
  color: var(--muted);
  font-size: 11px;
}

.custom-character > div {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 7px;
}

.custom-character input {
  width: 100%;
  min-height: 42px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: 11px;
  color: var(--ink);
  background: #fffdf8;
  font-size: 13px;
}

.custom-character .secondary {
  min-height: 42px;
  padding-inline: 13px;
  border-radius: 11px;
}

.branch-switch {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 11px;
  align-items: start;
  cursor: pointer;
}

.branch-switch.disabled {
  cursor: not-allowed;
  opacity: .62;
}

.branch-switch > input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.switch-track {
  width: 42px;
  height: 24px;
  margin-top: 1px;
  padding: 3px;
  border-radius: 999px;
  background: #d8d3c8;
  transition: background-color .18s ease;
}

.switch-track span {
  width: 18px;
  height: 18px;
  display: block;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 2px 6px rgba(60, 45, 26, .18);
  transition: transform .18s ease;
}

.branch-switch > input:checked + .switch-track {
  background: var(--leaf);
}

.branch-switch > input:checked + .switch-track span {
  transform: translateX(18px);
}

.branch-switch strong {
  display: block;
  color: var(--ink);
  font-size: 13px;
  font-weight: 600;
  line-height: 20px;
}

.branch-switch small {
  display: block;
  margin-top: 4px;
  color: var(--muted);
  font-size: 11px;
  line-height: 18px;
}

.workshop-submit {
  width: 100%;
  min-height: 50px;
  border-radius: 13px;
  font-size: 14px;
}

.workshop-form-note {
  margin: -10px 0 0;
  color: var(--muted);
  font-size: 11px;
  line-height: 18px;
  text-align: center;
}

.workshop-error {
  margin: 0;
  padding: 10px 12px;
  border-radius: 11px;
  color: var(--danger);
  background: var(--danger-soft);
  font-size: 12px;
  line-height: 19px;
}

.workshop-stage {
  min-height: 620px;
  overflow: hidden;
  border-radius: 26px;
}

.workshop-blank,
.workshop-state {
  min-height: 620px;
  display: grid;
  place-items: center;
  align-content: center;
  padding: 48px;
  text-align: center;
}

.workshop-blank {
  position: relative;
  background:
    radial-gradient(circle at 50% 38%, rgba(241, 203, 130, .18), transparent 17rem),
    linear-gradient(160deg, rgba(255, 252, 245, .86), rgba(242, 247, 238, .84));
}

.blank-book-scene {
  width: 250px;
  height: 210px;
  position: relative;
  margin-bottom: 28px;
}

.paper-sheet {
  width: 160px;
  height: 190px;
  position: absolute;
  left: 50%;
  top: 50%;
  border: 1px solid #dcc7a8;
  border-radius: 7px 16px 16px 7px;
  background: #fffdf7;
  box-shadow: 0 18px 30px rgba(77, 55, 31, .13);
}

.paper-sheet.back {
  transform: translate(-56%, -47%) rotate(-9deg);
  background: #edf3e9;
}

.paper-sheet.middle {
  transform: translate(-48%, -51%) rotate(6deg);
  background: #fff7e9;
}

.paper-sheet.front {
  display: grid;
  align-content: center;
  gap: 12px;
  padding: 28px;
  transform: translate(-50%, -52%);
}

.paper-sheet.front b {
  color: var(--brown-soft);
  font-size: 40px;
  line-height: 1;
}

.paper-sheet.front i {
  height: 5px;
  border-radius: 999px;
  background: #e8dcc9;
}

.paper-sheet.front i:nth-of-type(2) {
  width: 78%;
}

.paper-sheet.front i:nth-of-type(3) {
  width: 56%;
}

.scene-leaf {
  width: 64px;
  height: 92px;
  position: absolute;
  border-radius: 100% 0 100% 0;
  background: #dbe6d5;
}

.leaf-one {
  left: 9px;
  bottom: 0;
  transform: rotate(-24deg);
}

.leaf-two {
  right: 3px;
  top: 0;
  transform: rotate(146deg);
  background: #f2dfbd;
}

.workshop-blank h2,
.workshop-state h2 {
  margin: 0;
  font-family: var(--font-story);
  font-size: clamp(28px, 3.2vw, 42px);
  font-weight: 500;
  line-height: 1.22;
}

.workshop-blank > p:not(.eyebrow),
.workshop-state > p:not(.eyebrow) {
  max-width: 560px;
  margin: 14px 0 0;
  color: var(--muted);
  font-size: 14px;
  line-height: 24px;
}

.blank-facts {
  width: min(100%, 560px);
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  overflow: hidden;
  margin-top: 30px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: var(--line);
}

.blank-facts span {
  display: grid;
  gap: 3px;
  padding: 14px 10px;
  color: var(--muted);
  background: rgba(255, 253, 248, .92);
  font-size: 11px;
}

.blank-facts strong {
  color: var(--ink);
  font-family: var(--font-story);
  font-size: 17px;
  font-weight: 600;
}

.workshop-loading-state {
  background:
    radial-gradient(circle at 50% 30%, rgba(241, 203, 130, .22), transparent 18rem),
    #fffdf8;
}

.story-spinner {
  width: 108px;
  height: 108px;
  position: relative;
  display: grid;
  place-items: center;
  margin-bottom: 24px;
}

.story-spinner span {
  width: 88px;
  height: 64px;
  position: absolute;
  border: 1px solid #dcc8a9;
  border-radius: 5px 12px 12px 5px;
  background: rgba(255, 251, 242, .94);
  animation: workshop-page 2.2s ease-in-out infinite;
}

.story-spinner span:nth-child(2) {
  animation-delay: .25s;
}

.story-spinner span:nth-child(3) {
  animation-delay: .5s;
}

.draft-skeleton {
  width: min(100%, 520px);
  height: 150px;
  display: grid;
  gap: 13px;
  margin-top: 28px;
  padding: 24px;
  border: 1px solid var(--line);
  border-radius: 18px;
  background: rgba(255, 255, 255, .68);
  text-align: left;
}

.draft-skeleton span {
  height: 13px;
  border-radius: 999px;
  background: linear-gradient(90deg, #eee6da 25%, #faf5ec 45%, #eee6da 65%);
  background-size: 240% 100%;
  animation: workshop-shimmer 1.8s ease infinite;
}

.draft-skeleton span:first-child {
  width: 58%;
  height: 22px;
}

.draft-skeleton span:last-child {
  width: 74%;
}

.workshop-loading-state > small {
  margin-top: 14px;
  color: var(--muted);
  font-size: 11px;
  line-height: 18px;
}

.workshop-result-state {
  align-content: center;
}

.state-symbol,
.approved-symbol {
  width: 64px;
  height: 64px;
  display: grid;
  place-items: center;
  margin-bottom: 20px;
  border-radius: 50%;
  color: var(--brown);
  background: var(--sand);
  font-family: var(--font-story);
  font-size: 30px;
}

.error-state .state-symbol {
  color: var(--danger);
  background: var(--danger-soft);
}

.workshop-result-state .primary {
  margin-top: 22px;
}

.draft-review {
  padding: 26px;
}

.draft-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}

.draft-heading h2 {
  margin: 10px 0 0;
  font-family: var(--font-story);
  font-size: clamp(32px, 3.8vw, 50px);
  font-weight: 500;
  line-height: 1.12;
}

.draft-heading p {
  margin: 7px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 20px;
}

.draft-heading-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
}

.draft-continuation-badge {
  padding: 7px 10px;
  border-radius: 999px;
  color: var(--brown);
  background: var(--sand);
  font-size: 11px;
  font-weight: 600;
}

.carried-prefix-note {
  margin-top: 18px;
  padding: 12px 14px;
  border-left: 3px solid var(--leaf);
  border-radius: 0 11px 11px 0;
  color: var(--ink-soft);
  background: var(--leaf-wash);
  font-size: 12px;
  line-height: 20px;
}

.carried-prefix-note strong {
  margin-right: 8px;
  color: var(--leaf);
}

.check-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 20px 0;
  padding: 12px;
  border-radius: 14px;
  background: #f6f1e8;
}

.check-strip span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 9px;
  border-radius: 999px;
  color: var(--brown);
  background: #fffaf2;
  font-size: 11px;
}

.check-strip span.passed {
  color: var(--leaf);
  background: var(--leaf-pale);
}

.check-strip b {
  font-size: 12px;
}

.review-pages {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.review-page {
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 18px;
  background: #fffaf2;
}

.review-page figure {
  position: relative;
  aspect-ratio: 4 / 3;
  margin: 0;
  overflow: hidden;
  background: #edf1e8;
}

.review-page figure img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform .28s ease;
}

.review-page:hover figure img {
  transform: scale(1.025);
}

.page-origin {
  position: absolute;
  left: 10px;
  bottom: 10px;
  padding: 5px 8px;
  border-radius: 999px;
  color: var(--ink);
  background: rgba(255, 250, 240, .9);
  font-size: 10px;
  font-weight: 600;
}

.page-origin.new {
  color: #fff;
  background: var(--leaf);
}

.review-page-copy {
  padding: 16px 17px 18px;
}

.review-page-copy span {
  color: var(--brown-soft);
  font-size: 11px;
  font-weight: 700;
}

.review-page-copy p {
  margin: 8px 0 0;
  color: var(--ink-soft);
  font-family: var(--font-story);
  font-size: 15px;
  line-height: 26px;
}

.branch-review {
  margin-top: 20px;
  padding: 20px;
  border-radius: 20px;
  background: linear-gradient(145deg, #f4ecdf, #eef4e9);
}

.branch-review-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 20px;
}

.branch-review-heading h3 {
  margin: 0;
  font-family: var(--font-story);
  font-size: 23px;
  font-weight: 600;
  line-height: 32px;
}

.branch-review-heading > span {
  max-width: 180px;
  color: var(--muted);
  font-size: 11px;
  line-height: 18px;
  text-align: right;
}

.branch-group {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 16px;
}

.branch-option {
  padding: 15px;
  border: 1px solid rgba(205, 189, 166, .72);
  border-radius: 15px;
  background: rgba(255, 253, 248, .82);
}

.branch-label {
  display: inline-flex;
  padding: 5px 8px;
  border-radius: 999px;
  color: var(--brown);
  background: var(--sand);
  font-size: 11px;
  font-weight: 600;
}

.branch-option strong {
  display: block;
  margin-top: 10px;
  font-size: 13px;
}

.branch-option p {
  display: -webkit-box;
  overflow: hidden;
  margin: 7px 0 10px;
  color: var(--muted);
  font-size: 12px;
  line-height: 19px;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.branch-option small {
  color: var(--brown-soft);
  font-size: 10px;
}

.approval-panel,
.approved-panel {
  margin-top: 20px;
  padding: 22px;
  border-radius: 20px;
  background: #fff7e8;
}

.approval-panel h3,
.approved-panel h2 {
  margin: 0;
  font-family: var(--font-story);
  font-size: 25px;
  font-weight: 600;
  line-height: 34px;
}

.approval-panel > div > p:not(.eyebrow),
.approved-panel > div > p:not(.eyebrow) {
  margin: 7px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 22px;
}

.approval-check {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin: 18px 0 14px;
  color: var(--ink-soft);
  font-size: 13px;
  line-height: 21px;
  cursor: pointer;
}

.approval-check input {
  width: 18px;
  height: 18px;
  margin: 1px 0 0;
  accent-color: var(--leaf);
}

.approval-panel > .primary {
  width: 100%;
}

.approval-panel > .workshop-error,
.approved-panel > .workshop-error {
  margin: 10px 0;
}

.approved-panel {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
  background: linear-gradient(145deg, #eef5ea, #fff7e8);
}

.approved-panel .approved-symbol {
  margin: 0;
  color: #fff;
  background: var(--leaf);
}

.approved-actions {
  grid-column: 1 / -1;
  display: flex;
  flex-wrap: wrap;
  gap: 9px;
}

@keyframes workshop-pulse {
  50% { opacity: .3; }
}

@keyframes workshop-page {
  0%, 100% { transform: translateY(8px) rotate(-4deg) scale(.96); opacity: .38; }
  45% { transform: translateY(-3px) rotate(0) scale(1); opacity: 1; }
}

@keyframes workshop-shimmer {
  from { background-position: 100% 0; }
  to { background-position: -100% 0; }
}

@media (max-width: 1100px) {
  .workshop-heading {
    grid-template-columns: 1fr;
  }
  .workshop-rule-card {
    max-width: 680px;
  }
  .workshop-workspace {
    grid-template-columns: 1fr;
  }
  .workshop-form-card {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 700px) {
  .workshop-page {
    padding-top: 18px;
  }
  .workshop-heading {
    padding: 24px 20px;
    border-radius: 22px;
  }
  .workshop-heading h1 {
    font-size: 36px;
  }
  .workshop-heading-copy > p:not(.eyebrow) {
    font-size: 14px;
    line-height: 24px;
  }
  .workshop-heading-actions,
  .workshop-heading-actions button {
    width: 100%;
  }
  .workshop-rule-card {
    grid-template-columns: 40px minmax(0, 1fr);
    padding: 17px;
  }
  .rule-mark {
    width: 40px;
    height: 40px;
  }
  .workshop-workspace {
    margin-top: 14px;
  }
  .workshop-form-card,
  .draft-review {
    padding: 18px;
    border-radius: 20px;
  }
  .age-options {
    grid-template-columns: 1fr;
  }
  .age-options label {
    min-height: 58px;
    grid-template-columns: auto 1fr;
    align-items: center;
    text-align: left;
  }
  .custom-character > div,
  .review-pages,
  .branch-group {
    grid-template-columns: 1fr;
  }
  .workshop-stage,
  .workshop-blank,
  .workshop-state {
    min-height: 520px;
  }
  .workshop-blank,
  .workshop-state {
    padding: 28px 20px;
  }
  .blank-facts {
    grid-template-columns: 1fr;
  }
  .draft-heading,
  .branch-review-heading {
    align-items: flex-start;
    flex-direction: column;
  }
  .draft-heading-actions {
    justify-content: flex-start;
  }
  .branch-review-heading > span {
    text-align: left;
  }
  .approved-panel {
    grid-template-columns: 1fr;
  }
  .approved-actions {
    grid-column: auto;
    display: grid;
  }
  .approved-actions button,
  .approval-panel > .primary {
    width: 100%;
  }
}
</style>
