import { mediaUrl } from '../api/client'
import { formatBookAge } from './bookMetadata'

const covers = [
  '/assets/figma-prototype/home-main-cover.png',
  '/assets/figma-prototype/home-shelf-cover-1.webp',
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

function pagesFrom(texts) {
  return texts.map((text, index) => ({
    pageNumber: index + 1,
    text,
    question: questions[index % questions.length],
    answer: '可以请孩子先指出画面里的角色和动作，再尝试说出自己的理解。答案没有唯一标准，重要的是愿意表达和倾听。',
    imageRef: null,
    defaultNextPageNumber: index + 1 < texts.length ? index + 2 : null,
  }))
}

const definitions = [
  {
    storyVersionId: 1001,
    storybookId: 101,
    title: '树下留一个座位',
    ageBand: 'AGE_4_5',
    ageLabel: '3–6 岁',
    topics: ['友谊', '自然', '分享'],
    cover: covers[0],
    summary: '小熊在树下摆开坐垫，等森林里的邻居一起来听故事。',
    quote: '最后一个坐垫留给晚到的朋友。',
    texts: [
      '小熊抱着一本红书来到树下。',
      '它铺好三块坐垫，又从篮子里拿出一块。',
      '最后一个坐垫留给晚到的朋友。',
      '小鸟第一个到了，落在最低的树枝上。',
      '松鼠提来一篮松果，把篮子放在中间。',
      '小熊翻开书，读到一扇找不到钥匙的门。',
      '松鼠猜钥匙在门垫下面。',
      '小鸟猜钥匙藏在树洞里。',
      '草丛沙沙响，一只蜗牛探出头。',
      '小熊合上书，把空坐垫挪到蜗牛身旁。',
      '小鸟把门和钥匙的故事从头说了一遍。',
      '蜗牛伸长触角，指向画里窗台上的花盆。',
      '小熊翻到下一页，钥匙果然挂在花盆边。',
      '太阳快下山了，大家把坐垫一块块收好。',
      '蜗牛说，下回它想讲雨水敲屋顶的故事。',
    ],
  },
  {
    storyVersionId: 1002,
    storybookId: 1,
    title: '窗边的新书签',
    ageBand: 'AGE_3_4',
    ageLabel: '3–5 岁',
    topics: ['亲子依恋', '安全感', '想象力'],
    cover: covers[1],
    summary: '小满和妈妈用纸盒剪下一对兔耳朵，给读到一半的书做个记号。',
    quote: '再打开时，兔耳朵还在那一页等她。',
    texts: [
      '午后的阳光照到窗台上，小满读完了故事的第一章。',
      '她想记住这一页，就从空纸盒上剪下一张小卡片。',
      '妈妈扶住卡片，小满把卡片的两角剪成兔耳朵。',
      '小满给兔子点上眼睛，又画了一颗歪歪的纽扣。',
      '吃点心前，她让兔子夹在刚刚读过的那一页。',
      '再打开时，兔耳朵还在那一页等她。',
    ],
  },
  {
    storyVersionId: 1003,
    storybookId: 102,
    title: '小鸭和荷叶伞',
    ageBand: 'AGE_4_5',
    ageLabel: '3–6 岁',
    topics: ['友谊', '自然', '动物'],
    cover: covers[2],
    summary: '一场阵雨把小鸭和豆娘聚在同一片荷叶下。',
    quote: '等雨停了，你再飞也来得及。',
    texts: [
      '小鸭沿着池塘散步，红围巾在风里摆动。',
      '一滴水落在它的鼻尖，紧接着又来了一滴。',
      '小鸭钻进岸边的大荷叶下面。',
      '豆娘贴着水面飞来，翅膀沾了几滴水。',
      '小鸭往里面挪了挪，说：“等雨停了，你再飞也来得及。”',
      '它们听雨点打在荷叶上，数到七就笑着重新开始。',
      '荷叶边的水珠滴完了，豆娘爬到叶尖晾翅膀。',
      '小鸭抖抖围巾，和豆娘一起沿着池塘往前走。',
    ],
  },
  {
    storyVersionId: 1004,
    storybookId: 103,
    title: '书架上的迷路信',
    ageBand: 'AGE_5_6',
    ageLabel: '6–10 岁',
    topics: ['成长', '互助', '友谊'],
    cover: covers[3],
    summary: '阿禾在借来的书里发现一张便条，和图书管理员一起找到了写信人。',
    quote: '原来读过同一本书的人，也会看到不同的地方。',
    texts: [
      '阿禾从书架上抽出一本蓝色绘本。',
      '书里滑出一张纸，纸上画着一只黄色风筝。',
      '风筝下面写着：我最喜欢这片弯弯的云。',
      '阿禾翻了好几页，终于找到了那朵云。',
      '她最先看见的是云下那两条细细的风筝线。',
      '她把便条拿给图书管理员，请他帮忙寻找写信人。',
      '管理员在活动桌上挂起一块征集书画的小板。',
      '阿禾把便条夹上去，又画了两条风筝线。',
      '周六，一个小男孩站在小板前，认出了自己的画。',
      '他和阿禾回到书架旁，又一起翻开那本书。',
      '原来读过同一本书的人，也会看到不同的地方。',
      '两人把画收进活动画册，在下面各写了一句喜欢的理由。',
    ],
  },
  {
    storyVersionId: 1005,
    storybookId: 104,
    title: '草坡上的风铃',
    ageBand: 'AGE_3_4',
    ageLabel: '3–6 岁',
    topics: ['自然', '合作', '动物'],
    cover: covers[4],
    summary: '草坡旁的小屋少了一点声音，几个朋友拿来树枝和空杯子，试着做一串风铃。',
    quote: '等到风吹过来，小屋就有了自己的铃声。',
    texts: [
      '草坡上的小屋刚刷好门，小羊想给它挂串风铃。',
      '小獾拿来两个空杯子，放在窗台上。',
      '小鸟叼来一根枯树枝，小羊找出一团麻绳。',
      '大家把杯子系在树枝上，挂到窗边。',
      '风吹来了，两只杯子擦过彼此，一点声音也没有。',
      '小羊把绳子往上挪了挪，小獾在中间加了一根短树枝。',
      '杯子轻轻撞在树枝上，发出两声脆响。',
      '小鸟又衔来一片小叶子，系在最下面。',
      '叶子先摆起来，小羊就知道风要来了。',
      '等到风吹过来，小屋就有了自己的铃声。',
    ],
  },
  {
    storyVersionId: 1006,
    storybookId: 105,
    title: '圆圆的种子盒',
    ageBand: 'AGE_3_4',
    ageLabel: '3–6 岁',
    topics: ['成长', '自然', '家庭'],
    cover: covers[5],
    summary: '小兔把在散步时捡到的种子分开放好，准备和家人一起种下。',
    quote: '小兔给每一格画了一个记号。',
    texts: [
      '散步回来，小兔打开奶奶给它的圆盒子。',
      '盒里有三种种子，有的像豆子，有的像小船。',
      '小兔把种子倒在桌上，一粒藏进了桌布的褶里。',
      '奶奶托住桌布，小兔摸到了那粒细细的种子。',
      '它们用纸条把盒子分成三格，把种子一粒粒放回去。',
      '小兔给每一格画了一个记号。',
      '后天要下雨，它们约好等雨停了就一起种种子。',
    ],
  },
]

export const previewBooks = import.meta.env.MODE === 'public' ? [] : definitions.map(({ texts, ...definition }) => ({
  ...definition,
  author: '知芽示例',
  id: `preview-${definition.storyVersionId}`,
  status: 'PREVIEW',
  source: 'preview',
  pageCount: texts.length,
  pages: pagesFrom(texts),
}))

export { ageLabels } from './bookMetadata'

export function mapApiBook(version) {
  const pages = (version.pages || []).map((page) => ({ ...page }))
  return {
    id: `version-${version.storyVersionId}`,
    source: 'api',
    storyVersionId: version.storyVersionId,
    storybookId: version.storybookId,
    versionNumber: version.versionNumber,
    title: version.title,
    author: version.author || '作者信息暂缺',
    ageBand: version.ageBand || '',
    ageLabel: formatBookAge(version),
    topics: version.topics || [],
    cover: mediaUrl(version.coverRef, 'thumbnail') || '/assets/image-placeholder.svg',
    coverRef: version.coverRef,
    summary: version.summary || pages[0]?.text || '内容简介暂缺',
    quote: '',
    pageCount: pages.length,
    pages,
    branches: version.branches || [],
    status: version.status,
    sourceStoryVersionId: version.sourceStoryVersionId || null,
    continuation: version.continuation || null,
  }
}

export function bookStatusLabel(status) {
  return { PREVIEW: '设计预览', PUBLISHED: '公开已发布', APPROVED_PRIVATE: '本人已审核私有绘本', RETIRED: '历史版本' }[status] || '状态待确认'
}

export { covers }
