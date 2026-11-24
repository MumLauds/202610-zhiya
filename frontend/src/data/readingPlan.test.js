import { beforeEach, expect, it } from 'vitest'
import { readPlan, readPlanProgress, readingProfileKey, recordPlanActivity, storePlan } from './readingPlan'

beforeEach(() => localStorage.clear())

it('uses the saved target for the matching child and honors the preferred default', () => {
  const child = readingProfileKey(8, 2)
  expect(readPlan(child, 12).minutes).toBe(12)
  expect(storePlan(child, { minutes: 20, books: 2, weekly: 3 })).toBe(true)
  expect(readPlan(child, 12)).toEqual({ minutes: 20, books: 2, weekly: 3 })
  expect(readPlan(readingProfileKey(8, 3), 12).minutes).toBe(12)
  expect(storePlan(child, { minutes: 0, books: 2, weekly: 3 })).toBe(false)
})

it('tracks elapsed minutes, deduplicates completed sessions, and separates local days', () => {
  const child = readingProfileKey(3, 4)
  const afterMidnight = new Date(2026, 10, 18, 0, 0, 30)
  recordPlanActivity(child, afterMidnight.getTime(), 60000, 25)
  recordPlanActivity(child, afterMidnight.getTime() + 30000, 30000, 25)
  expect(readPlanProgress(child, afterMidnight)).toEqual({ minutes: 1, books: 1, weekly: 2 })
  expect(readPlanProgress(readingProfileKey(5, 4), afterMidnight)).toEqual({ minutes: 0, books: 0, weekly: 0 })
})

it('merges overlapping intervals after midnight and preserves earlier saved totals', () => {
  const key = '8.2'
  localStorage.setItem('zhiya.readingActivity.8.2', JSON.stringify({
    '2026-11-18': { milliseconds: 60_000, completed: [25] },
  }))
  const midnight = new Date(2026, 10, 18).getTime()
  recordPlanActivity(key, midnight + 40_000, 60_000, 25)
  recordPlanActivity(key, midnight + 80_000, 70_000)
  recordPlanActivity(key, midnight + 120_000, 20_000)
  expect(readPlanProgress(key, new Date(midnight + 120_000))).toEqual({ minutes: 2, books: 1, weekly: 2 })
})
