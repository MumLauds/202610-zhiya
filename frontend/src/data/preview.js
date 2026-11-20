import { mediaUrl } from '../api/client'

const covers = [
  '/assets/figma-prototype/home-main-cover.png',
  '/assets/figma-prototype/home-shelf-cover-1.png',
  '/assets/figma-prototype/home-shelf-cover-2.png',
  '/assets/figma-prototype/library-featured-cover.png',
  '/assets/book-cover.svg',
  '/assets/reader-scene.svg',
]

const questions = [
  '你觉得这一页里，谁最需要帮助？',
  '如果你也在画面里，你想做什么？',
  '故事里的人物为什么会这样选择？',
  '接下来可能会发生什么？',
]

function pagesFrom(book, seed = []) {
  return Array.from({ length: book.pageCount }, (_, index) => ({
    pageNumber: index + 1,
    text:
      seed[index] ||
      (index === 0
        ? `${book.title}的故事从一段安静的相遇开始。${book.summary}`
        : '故事里的小主人公继续往前走，和朋友一起寻找答案，也慢慢学会把自己的感受说出来。'),
    question: questions[index % questions.length],
    answer: '可以请孩子先指出画面里的角色和动作，再尝试说出自己的理解。答案没有唯一标准，重要的是愿意表达和倾听。',
    imageRef: null,
    defaultNextPageNumber: index + 1 < book.pageCount ? index + 2 : null,
  }))
}

const bunnySeeds = [
  '从前有一只小兔子，他很想要离家出走。有一天，他对妈妈说：“我要跑走啦！”',
  '“如果你跑走了，”妈妈说，“我就要去追你，因为你是我的小宝贝呀！”',
  '“如果你来追我，”小兔子说，“我就要变成溪里的小鳟鱼，游得远远的。”',
  '“如果你变成溪里的小鳟鱼，”妈妈说，“我就变成捕鱼的人去抓你。”',
  '“如果你变成捕鱼的人，”小兔子说，“我就要变成高山上的大石头，让你抓不到我。”',
  '“如果你变成高山上的大石头，”妈妈说，“我就变成爬山的人，爬到高山上去找你。”',
]

const definitions = [
  {
    storyVersionId: 1001,
    storybookId: 101,
    title: '小熊的森林故事会',
    author: '[英] 利兹·布朗',
    ageBand: 'AGE_4_5',
    ageLabel: '3–6 岁',
    topics: ['友谊', '自然', '分享'],
    pageCount: 15,
    cover: covers[0],
    summary: '这是一个关于阅读、友谊与分享的温暖故事，让孩子在轻松有趣的情节中感受阅读的快乐。',
    quote: '把喜欢的故事讲给别人听，快乐就会变成两份。',
  },
  {
    storyVersionId: 1002,
    storybookId: 1,
    title: '逃家小兔',
    author: '[美] 玛格丽特·怀兹·布朗',
    ageBand: 'AGE_3_4',
    ageLabel: '3–5 岁',
    topics: ['亲子依恋', '安全感', '想象力'],
    pageCount: 6,
    cover: covers[1],
    summary: '小兔子说想跑走，妈妈用温柔又聪明的方式一路追着它。',
    quote: '如果你跑走了，我就去追你，因为你是我的小宝贝呀。',
    seeds: bunnySeeds,
  },
  {
    storyVersionId: 1003,
    storybookId: 102,
    title: '猜猜我有多爱你',
    author: '[爱尔兰] 山姆·麦克布雷尼',
    ageBand: 'AGE_4_5',
    ageLabel: '3–6 岁',
    topics: ['亲子依恋', '自然', '动物'],
    pageCount: 8,
    cover: covers[2],
    summary: '小兔子和大兔子比赛谁的爱更多，在温柔的想象里感受彼此的爱。',
    quote: '我爱你，一直到月亮那里，再从月亮上回到这里来。',
  },
  {
    storyVersionId: 1004,
    storybookId: 103,
    title: '小王子',
    author: '[法] 安托万·德·圣埃克苏佩里',
    ageBand: 'AGE_5_6',
    ageLabel: '6–10 岁',
    topics: ['童话', '成长', '友谊'],
    pageCount: 12,
    cover: covers[3],
    summary: '一个关于爱、陪伴与责任的成长故事，让孩子在星球旅途中理解什么才是真正重要的。',
    quote: '只有用心，才能看得清楚。真正重要的东西，用眼睛是看不见的。',
  },
  {
    storyVersionId: 1005,
    storybookId: 104,
    title: '月亮的味道',
    author: '麦克·格雷涅茨',
    ageBand: 'AGE_3_4',
    ageLabel: '3–6 岁',
    topics: ['自然', '合作', '动物'],
    pageCount: 10,
    cover: covers[4],
    summary: '小动物们一个叠着一个，想尝一尝月亮的味道。',
    quote: '大家一起想办法，远一点的目标也会变得近一点。',
  },
  {
    storyVersionId: 1006,
    storybookId: 105,
    title: '大卫，不可以',
    author: '大卫·香农',
    ageBand: 'AGE_3_4',
    ageLabel: '3–6 岁',
    topics: ['成长', '规则', '家庭'],
    pageCount: 7,
    cover: covers[5],
    summary: '在一次次“不可以”的提醒里，孩子学会边界，也确认自己一直被爱着。',
    quote: '爱不是没有规则，而是一直在身旁。',
  },
]

export const previewBooks = definitions.map((definition) => ({
  ...definition,
  id: `preview-${definition.storyVersionId}`,
  status: 'PREVIEW',
  source: 'preview',
  pages: pagesFrom(definition, definition.seeds),
}))

export const ageLabels = { AGE_3_4: '3–4 岁', AGE_4_5: '4–5 岁', AGE_5_6: '5–6 岁' }

export function mapApiBook(version) {
  const pages = (version.pages || []).map((page) => ({ ...page }))
  return {
    id: `version-${version.storyVersionId}`,
    source: 'api',
    storyVersionId: version.storyVersionId,
    storybookId: version.storybookId,
    title: version.title,
    author: version.author || '作者信息暂缺',
    ageBand: version.ageBand || '',
    ageLabel: ageLabels[version.ageBand] || '适龄信息暂缺',
    topics: version.topics || [],
    cover: mediaUrl(version.coverRef) || '/assets/image-placeholder.svg',
    coverRef: version.coverRef,
    summary: version.summary || pages[0]?.text || '内容简介暂缺',
    quote: '',
    pageCount: pages.length,
    pages,
    branches: version.branches || [],
    status: version.status,
  }
}

export function bookStatusLabel(status) {
  return { PREVIEW: '设计预览', PUBLISHED: '公开已发布', APPROVED_PRIVATE: '本人已审核私有绘本', RETIRED: '历史版本' }[status] || '状态待确认'
}

export { covers }
