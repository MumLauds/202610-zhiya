import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'

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
