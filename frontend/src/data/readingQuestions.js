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

const yayaActionLabels = {
  LISTEN: '芽芽正在陪孩子看。',
  PAUSE: '共读引导已暂停。',
  CLOSE_TOPIC: '这一轮话题已结束。',
  CONTINUE: '可以继续共读。',
}

export function describeYayaReceipt(receipt) {
  if (receipt?.version !== '2.0') return ''
  if (receipt.reason === 'API_TIMEOUT') return '芽芽回应超时，可以继续看绘本。'
  return yayaActionLabels[receipt.action] ?? yayaActionLabels[receipt.state] ?? ''
}
