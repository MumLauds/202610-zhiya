import { computed, onMounted, onUnmounted, shallowRef } from 'vue'

const limits = { minutes: 60, books: 5, weekly: 7 }
const activityKey = key => `zhiya.readingActivity.${key}`
const planKey = key => key ? `zhiya.plan.${key}` : 'zhiya.plan'

export function readingProfileKey(accountId, profileId) {
  return accountId && profileId ? `${accountId}.${profileId}` : null
}

function readStored(key) {
  try { return JSON.parse(localStorage.getItem(key)) } catch { return null }
}

function writeStored(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); return true } catch { return false }
}

export function readPlan(key, preferredMinutes = 15) {
  const plan = { minutes: preferredMinutes, books: 1, weekly: 5 }
  const saved = readStored(planKey(key)) ?? readStored('zhiya.plan')
  for (const [field, limit] of Object.entries(limits)) {
    const value = saved?.[field]
    if (Number.isInteger(value) && value >= 1 && value <= limit) plan[field] = value
  }
  return plan
}

export function storePlan(key, plan) {
  if (Object.entries(limits).some(([field, max]) =>
    !Number.isInteger(plan[field]) || plan[field] < 1 || plan[field] > max)) return false
  return writeStored(planKey(key), plan)
}

function dayKey(date) {
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${month}-${day}`
}

function weekStart(date) {
  const start = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  start.setDate(start.getDate() - (start.getDay() + 6) % 7)
  return dayKey(start)
}

export function readPlanProgress(key, now = new Date()) {
  const activity = key ? readStored(activityKey(key)) ?? {} : {}
  const today = dayKey(now)
  const day = activity[today]
  const monday = weekStart(now)
  return {
    minutes: Math.floor((day?.milliseconds ?? 0) / 60000),
    books: day?.completed?.length ?? 0,
    weekly: Object.entries(activity).filter(([date, entry]) =>
      date >= monday && date <= today && (entry.milliseconds > 0 || entry.completed?.length)).length,
  }
}

export function useReadingPlanProgress(profileKey) {
  const now = shallowRef(new Date())
  let midnightTimer

  function updateDate() {
    clearTimeout(midnightTimer)
    const date = new Date()
    now.value = date
    const nextMidnight = new Date(date.getFullYear(), date.getMonth(), date.getDate() + 1)
    midnightTimer = setTimeout(updateDate, nextMidnight - date)
  }

  onMounted(() => {
    updateDate()
    document.addEventListener('visibilitychange', updateDate)
  })
  onUnmounted(() => {
    clearTimeout(midnightTimer)
    document.removeEventListener('visibilitychange', updateDate)
  })

  return computed(() => readPlanProgress(profileKey.value, now.value))
}

export function recordPlanActivity(key, ended, milliseconds, completedSession) {
  if (!key || (!milliseconds && !completedSession)) return
  const activity = readStored(activityKey(key)) ?? {}
  const date = new Date(ended)
  const today = dayKey(date)
  const monday = weekStart(date)
  const previous = dayKey(new Date(ended - milliseconds))
  const midnight = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
  const previousMilliseconds = Math.max(0, midnight - (ended - milliseconds))
  if (previousMilliseconds && previous >= monday) {
    const previousDay = activity[previous] ??= { milliseconds: 0, completed: [] }
    previousDay.milliseconds += previousMilliseconds
  }
  const day = activity[today] ??= { milliseconds: 0, completed: [] }
  day.milliseconds += milliseconds - previousMilliseconds
  if (completedSession && !day.completed.includes(completedSession)) day.completed.push(completedSession)
  for (const dateKey of Object.keys(activity)) {
    if (dateKey < monday) delete activity[dateKey]
  }
  writeStored(activityKey(key), activity)
}
