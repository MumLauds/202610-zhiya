import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'

function readPositions(model, binary, accessorIndex) {
  const accessor = model.accessors[accessorIndex]
  const offset = entry => binary.byteOffset + (model.bufferViews[entry.bufferView].byteOffset ?? 0) + (entry.byteOffset ?? 0)
  const out = new Float32Array(accessor.count * 3)
  if (accessor.bufferView !== undefined) out.set(new Float32Array(binary.buffer, offset(accessor), out.length))
  if (accessor.sparse) {
    const { count, indices, values } = accessor.sparse
    const IndexArray = { 5121: Uint8Array, 5123: Uint16Array, 5125: Uint32Array }[indices.componentType]
    const destinations = new IndexArray(binary.buffer, offset(indices), count)
    const data = new Float32Array(binary.buffer, offset(values), count * 3)
    destinations.forEach((index, i) => out.set(data.subarray(i * 3, i * 3 + 3), index * 3))
  }
  return out
}

it('brings the central mouth opening into contact at rest and releases it for speech', () => {
  const bytes = readFileSync(resolve('public/assets/mascot/yaya-reading.glb'))
  const jsonEnd = 20 + bytes.readUInt32LE(12)
  const model = JSON.parse(bytes.toString('utf8', 20, jsonEnd))
  const binary = bytes.subarray(jsonEnd + 8)
  const mesh = model.meshes[0]
  const face = mesh.primitives[0]
  const rest = readPositions(model, binary, face.attributes.POSITION)
  const jaw = readPositions(model, binary, face.targets[mesh.extras.targetNames.indexOf('MouthOpen')].POSITION)
  const closedHeights = [], openHeights = []
  // Fixed landmarks inside the central lip opening of this rig, in its full-open pose.
  for (let i = 0; i < rest.length; i += 3) {
    const x = rest[i] + jaw[i], y = rest[i + 1] + jaw[i + 1], z = rest[i + 2] + jaw[i + 2]
    if (Math.abs(x) < .008 && y > .552 && y < .576 && z > .15) {
      openHeights.push(y)
      closedHeights.push(rest[i + 1])
    }
  }
  expect(openHeights.length).toBeGreaterThan(20)
  expect(Math.max(...openHeights) - Math.min(...openHeights)).toBeGreaterThan(.017)
  expect(Math.max(...closedHeights) - Math.min(...closedHeights)).toBeLessThan(.0008)
})

it('blinks on a single face mesh by changing only its painted texture', () => {
  const bytes = readFileSync(resolve('public/assets/mascot/yaya-reading.glb'))
  const model = JSON.parse(bytes.toString('utf8', 20, 20 + bytes.readUInt32LE(12)))
  expect(model.meshes).toHaveLength(1)
  const [mesh] = model.meshes
  expect(mesh.primitives).toHaveLength(1)
  expect(mesh.extras.targetNames).toEqual(['MouthOpen', 'MouthRound'])
  const [face] = mesh.primitives
  const open = model.materials[face.material]
  const variants = model.extensions.KHR_materials_variants.variants
  const textures = new Set([open.pbrMetallicRoughness.baseColorTexture.index])
  const paintedLight = new Set([open.emissiveTexture.index])
  for (const closure of [25, 50, 75, 100]) {
    const variant = variants.findIndex(item => item.name === `Eyes_${closure}`)
    const mapping = face.extensions.KHR_materials_variants.mappings.find(item => item.variants.includes(variant))
    const painted = structuredClone(model.materials[mapping.material])
    textures.add(painted.pbrMetallicRoughness.baseColorTexture.index)
    paintedLight.add(painted.emissiveTexture.index)
    painted.name = open.name
    painted.emissiveTexture.index = open.emissiveTexture.index
    painted.pbrMetallicRoughness.baseColorTexture.index = open.pbrMetallicRoughness.baseColorTexture.index
    expect(painted).toEqual(open)
  }
  expect(textures.size).toBe(5)
  expect(paintedLight.size).toBe(5)
})
