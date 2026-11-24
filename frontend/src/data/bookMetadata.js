export const ageLabels = { AGE_3_4: '3–4 岁', AGE_4_5: '4–5 岁', AGE_5_6: '5–6 岁' }

export function formatBookAge({ ageMin, ageMax, ageBand }) {
  if (Number.isInteger(ageMin) && Number.isInteger(ageMax) && ageMin <= ageMax) {
    return ageMin === ageMax ? `${ageMin} 岁` : `${ageMin}–${ageMax} 岁`
  }
  return ageLabels[ageBand] || '适龄信息暂缺'
}
