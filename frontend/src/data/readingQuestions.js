export function latestReadingQuestion(events, guidance) {
  // An answer that adds no question does not replace the active prompt.
  const followup = events.findLast(event => event.eventType === 'ANSWER'
    && typeof event.followupQuestion === 'string' && event.followupQuestion.trim())
  return followup && (!guidance || followup.interactionEventId > guidance.interactionEventId)
    ? { interactionEventId: followup.interactionEventId, eventType: 'ANSWER', outputText: followup.followupQuestion }
    : guidance
}

export function holdsYayaPrompt(answer, question) {
  return answer?.agentReceipt?.version === '2.0'
    && ['LISTEN', 'PAUSE', 'END_TOPIC'].includes(answer.agentReceipt.state)
    && answer.interactionEventId >= (question?.interactionEventId ?? 0)
}

export const yayaFallbackLabels = {
  API_TIMEOUT: '芽芽回应超时',
  API_CONNECTION_ERROR: '芽芽暂时连不上回应服务',
  API_HTTP_ERROR: '芽芽的回应服务暂时异常',
  MODEL_OUTPUT_INVALID: '芽芽的回应没有说完整',
  QUESTION_FORMAT: '芽芽这次的提问没有整理好',
  BUDGET_LIMIT: '芽芽今天的回应额度已用完',
  CLIENT_DISCONNECTED: '连接中断',
  DEPENDENCY_UNAVAILABLE: '芽芽暂时无法生成回应',
  SAFETY_BLOCK: '请家长先一起看看',
}

const yayaActionLabels = {
  LISTEN: '芽芽正在陪孩子看。',
  PAUSE: '共读引导已暂停。',
  CLOSE_TOPIC: '这一轮话题已结束。',
  CONTINUE: '可以继续共读。',
}

export function describeYayaReceipt(receipt) {
  if (receipt?.version !== '2.0') return ''
  const fallback = receipt.decision === 'FALLBACK' && yayaFallbackLabels[receipt.reason]
  if (fallback) return `${fallback}。`
  return yayaActionLabels[receipt.action] ?? yayaActionLabels[receipt.state] ?? ''
}
