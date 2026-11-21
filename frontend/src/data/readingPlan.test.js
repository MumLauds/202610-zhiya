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
