import { computed, onMounted, onUnmounted, shallowRef, watch } from 'vue'

const limits = { minutes: 60, books: 5, weekly: 7 }
const activityKey = key => `zhiya.readingActivity.${key}`
const planKey = key => key ? `zhiya.plan.${key}` : 'zhiya.plan'
const activityRevision = shallowRef(0)

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
  return start
}

function nextMidnight(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + 1).getTime()
}

function duration(day) {
  const measured = (day?.intervals ?? []).reduce((total, [start, end]) => total + end - start, 0)
  return (day?.milliseconds ?? 0) + measured
}

export function readPlanProgress(key, now = new Date()) {
  const activity = key ? readStored(activityKey(key)) ?? {} : {}
  const today = dayKey(now)
  const day = activity[today]
  const monday = dayKey(weekStart(now))
  return {
    minutes: Math.floor(duration(day) / 60000),
    books: day?.completed?.length ?? 0,
    weekly: Object.entries(activity).filter(([date, entry]) =>
      date >= monday && date <= today && (duration(entry) > 0 || entry.completed?.length)).length,
  }
}

export function useReadingPlanProgress(profileKey) {
  const now = shallowRef(new Date())
  let midnightTimer

  function updateDate() {
    clearTimeout(midnightTimer)
    const date = new Date()
    now.value = date
    midnightTimer = setTimeout(updateDate, nextMidnight(date) - date)
  }

  function syncActivity(event) {
    if (event.key === null || event.key === activityKey(profileKey.value)) updateDate()
  }

  // The reader's final checkpoint may finish after the next page mounts.
  watch(activityRevision, updateDate)
  onMounted(() => {
    updateDate()
    document.addEventListener('visibilitychange', updateDate)
    window.addEventListener('storage', syncActivity)
  })
  onUnmounted(() => {
    clearTimeout(midnightTimer)
    document.removeEventListener('visibilitychange', updateDate)
    window.removeEventListener('storage', syncActivity)
  })

  return computed(() => readPlanProgress(profileKey.value, now.value))
}

function mergeInterval(intervals, start, end) {
  const merged = []
  for (const [from, to] of [...intervals, [start, end]].sort(([left], [right]) => left - right)) {
    const previous = merged.at(-1)
    if (previous && from <= previous[1]) previous[1] = Math.max(previous[1], to)
    else merged.push([from, to])
  }
  return merged
}

export function recordPlanActivity(key, ended, milliseconds, completedSession) {
  if (!key || (!milliseconds && !completedSession)) return
  const storageKey = activityKey(key)
  function save() {
    const activity = readStored(storageKey) ?? {}
    const date = new Date(ended)
    const monday = weekStart(date)
    let start = Math.max(ended - milliseconds, monday.getTime())
    while (start < ended) {
      const startDate = new Date(start)
      const end = Math.min(ended, nextMidnight(startDate))
      const day = activity[dayKey(startDate)] ??= { milliseconds: 0, completed: [] }
      // Existing totals have no timestamps. Keep them as a separate baseline.
      day.intervals = mergeInterval(day.intervals ?? [], start, end)
      start = end
    }
    if (completedSession) {
      const day = activity[dayKey(date)] ??= { milliseconds: 0, completed: [] }
      if (!day.completed.includes(completedSession)) day.completed.push(completedSession)
    }
    const firstDay = dayKey(monday)
    for (const dateKey of Object.keys(activity)) {
      if (dateKey < firstDay) delete activity[dateKey]
    }
    if (writeStored(storageKey, activity)) activityRevision.value += 1
  }

  // Serialize the shared localStorage read/merge/write across visible windows.
  if (navigator.locks?.request) return navigator.locks.request(storageKey, save).catch(save)
  save()
}
