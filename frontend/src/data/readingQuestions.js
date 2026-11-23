export function latestReadingQuestion(events, guidance) {
  // An answer that adds no question does not replace the active prompt.
  const followup = events.findLast(event => event.eventType === 'ANSWER'
    && typeof event.followupQuestion === 'string' && event.followupQuestion.trim())
  return followup && (!guidance || followup.interactionEventId > guidance.interactionEventId)
    ? { interactionEventId: followup.interactionEventId, eventType: 'ANSWER', outputText: followup.followupQuestion }
    : guidance
}
