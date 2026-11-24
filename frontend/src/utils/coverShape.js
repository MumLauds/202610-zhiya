const clamp = (value, min, max) => Math.min(max, Math.max(min, value))

export function roundedTrapezoidPath(topLength = 96.6, bottomLength = 100, corner = 4) {
  const top = clamp(Number(topLength) / 100, .05, 1)
  const bottom = clamp(Number(bottomLength) / 100, .05, 1)
  const radius = clamp(Number(corner) / 100, 0, .12)
  const topLeft = (1 - top) / 2
  const topRight = (1 + top) / 2
  const bottomLeft = (1 - bottom) / 2
  const bottomRight = (1 + bottom) / 2

  const topRightOnSide = topRight + (bottomRight - topRight) * radius
  const bottomRightOnSide = bottomRight + (topRight - bottomRight) * radius
  const bottomLeftOnSide = bottomLeft + (topLeft - bottomLeft) * radius
  const topLeftOnSide = topLeft + (bottomLeft - topLeft) * radius

  return [
    `M ${(topLeft + radius).toFixed(5)} 0`,
    `L ${(topRight - radius).toFixed(5)} 0`,
    `Q ${topRight.toFixed(5)} 0 ${topRightOnSide.toFixed(5)} ${radius.toFixed(5)}`,
    `L ${bottomRightOnSide.toFixed(5)} ${(1 - radius).toFixed(5)}`,
    `Q ${bottomRight.toFixed(5)} 1 ${(bottomRight - radius).toFixed(5)} 1`,
    `L ${(bottomLeft + radius).toFixed(5)} 1`,
    `Q ${bottomLeft.toFixed(5)} 1 ${bottomLeftOnSide.toFixed(5)} ${(1 - radius).toFixed(5)}`,
    `L ${topLeftOnSide.toFixed(5)} ${radius.toFixed(5)}`,
    `Q ${topLeft.toFixed(5)} 0 ${(topLeft + radius).toFixed(5)} 0`,
    'Z',
  ].join(' ')
}
