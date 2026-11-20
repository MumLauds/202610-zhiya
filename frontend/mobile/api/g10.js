export const G10_STATUS_LABELS = {
  GENERATED: '已生成',
  FALLBACK: '安全回退',
  BLOCKED: '未生成',
}

export const STRATEGY_LABELS = {
  CONTEXT_QUESTION: '情境提问',
  CONCRETE_RECALL: '具体回顾',
  SKIP: '已跳过',
  TIMEOUT_FALLBACK: '超时回退',
  NO_KNOWLEDGE_FALLBACK: '无知识依据回退',
  SAFETY_BLOCK: '安全阻断',
}

export function normalizeAgentResult(data) {
  if (!data) return null
  return {
    interactionEventId: data.interactionEventId ?? null,
    revision: data.revision ?? null,
    status: data.status ?? 'BLOCKED',
    outputText: data.outputText ?? '',
    strategyType: data.strategyType ?? null,
    sources: Array.isArray(data.sources) ? data.sources : [],
  }
}
