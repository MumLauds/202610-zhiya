import {
  AnimationMixer, Clock, DirectionalLight, HemisphereLight, LoopOnce, OrthographicCamera,
  Scene, WebGLRenderer,
} from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'

export async function createYaya(host, state, sampleVoice, onLost) {
  const renderer = new WebGLRenderer({ alpha: true, antialias: true, powerPreference: 'low-power' })
  let asset
  try { asset = await new GLTFLoader().loadAsync('/assets/mascot/yaya-reading.glb') }
  catch (error) { renderer.dispose(); throw error }
  const scene = new Scene()
  scene.add(asset.scene, new HemisphereLight(0xfffaf1, 0xeadfcd, 1.85))
  const light = new DirectionalLight(0xfff5e4, 1.05)
  light.position.set(-2, 4, 4)
  scene.add(light)
  const camera = new OrthographicCamera(-.5, .5, .525, -.525, .1, 8)
  camera.position.set(.13, .56, 2.5)
  camera.lookAt(0, .50, 0)
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.setClearColor(0, 0)
  host.appendChild(renderer.domElement)
  function resize() {
    const { clientWidth: width, clientHeight: height } = host
    if (!width || !height) return
    camera.left = -(camera.top * width / height)
    camera.right = -camera.left
    camera.updateProjectionMatrix()
    renderer.setSize(width, height, false)
  }
  const observer = new ResizeObserver(resize)
  observer.observe(host)
  resize()
  const eyes = [], mouths = []
  asset.scene.traverse(node => {
    if (node.morphTargetDictionary?.Blink !== undefined) eyes.push(node)
    if (node.morphTargetDictionary?.MouthOpen !== undefined) mouths.push(node)
  })
  const head = asset.scene.getObjectByName('Head')
  const mixer = new AnimationMixer(asset.scene)
  const hello = mixer.clipAction(asset.animations.find(clip => clip.name === 'Wave'))
  hello.setLoop(LoopOnce, 1)
  const still = window.matchMedia('(prefers-reduced-motion: reduce)')
  function wave() { hello.reset().play() }
  const clock = new Clock()
  let time = 0, nextBlink = 2, lastRender = -1, visible = true
  let jaw = 0, rounding = 0
  const visibility = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting })
  visibility.observe(host)
  function draw() {
    const delta = Math.min(clock.getDelta(), .1)
    if (!visible || document.hidden) return
    time += delta
    if (time - lastRender < 1 / 30) return
    const step = Math.min(.1, time - lastRender)
    lastRender = time
    mixer.update(step)
    if (!hello.isRunning()) {
      const attention = state.listening ? -.12 : state.thinking ? .085 : 0
      head.rotation.z += (attention - head.rotation.z) * Math.min(1, step * 7)
      head.rotation.x = still.matches ? 0 : .018 * Math.sin(time * 1.65)
      head.rotation.y = still.matches ? 0 : .025 * Math.sin(time * .83)
      asset.scene.scale.y = still.matches ? 1 : 1 + .003 * Math.sin(time * 1.65)
    }
    const dt = time - nextBlink
    // Close quickly, hold briefly, then release the lids more slowly.
    const phase = dt < .075 ? dt / .075 : Math.min(1, (.235 - dt) / .13)
    const eyelid = still.matches || phase < 0 ? 0 : phase * phase * (3 - 2 * phase)
    if (dt > .235) nextBlink = time + 2.7 + Math.random() * 2.1
    for (const eye of eyes) eye.morphTargetInfluences[eye.morphTargetDictionary.Blink] = eyelid
    const mouth = sampleVoice()
    const active = state.media || state.speaking
    jaw = active ? jaw + (mouth.open - jaw) * (1 - Math.exp(-step * 21)) : 0
    rounding = active ? rounding + (mouth.round - rounding) * (1 - Math.exp(-step * 15)) : 0
    for (const face of mouths) {
      face.morphTargetInfluences[face.morphTargetDictionary.MouthOpen] = jaw
      face.morphTargetInfluences[face.morphTargetDictionary.MouthRound] = rounding
    }
    renderer.render(scene, camera)
  }
  function lost() { renderer.setAnimationLoop(null); renderer.domElement.hidden = true; onLost() }
  renderer.domElement.addEventListener('webglcontextlost', lost)
  if (!still.matches) wave()
  renderer.setAnimationLoop(draw)
  return {
    wave,
    dispose() {
      renderer.setAnimationLoop(null)
      renderer.domElement.removeEventListener('webglcontextlost', lost)
      visibility.disconnect()
      observer.disconnect()
      mixer.stopAllAction()
      mixer.uncacheRoot(asset.scene)
      asset.scene.traverse(node => {
        if (!node.isMesh) return
        node.geometry.dispose()
        node.skeleton?.dispose()
        node.material.map?.source.data?.close?.()
        node.material.map?.dispose()
        node.material.dispose()
      })
      renderer.domElement.remove()
      renderer.dispose()
    },
  }
}
