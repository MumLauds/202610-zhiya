const fs = require('fs')
const path = require('path')

const root = path.resolve(__dirname, '..')
const outputDir = path.join(root, 'html-review')
const htmlPath = path.join(outputDir, 'index.html')
let html = fs.readFileSync(htmlPath, 'utf8')

html = html.replace(/<link rel="stylesheet"[^>]*href="\.\/([^"]+\.css)"[^>]*>/g, (_match, file) => {
  const css = fs.readFileSync(path.join(outputDir, file), 'utf8')
  return `<style>\n${css}\n</style>`
})

html = html.replace(/<script type="module"[^>]*src="\.\/([^"]+\.js)"[^>]*><\/script>/g, (_match, file) => {
  const js = fs.readFileSync(path.join(outputDir, file), 'utf8').replace(/<\/script/gi, '<\\/script')
  return `<script type="module">\n${js}\n</script>`
})

fs.writeFileSync(htmlPath, html, 'utf8')

const assetsDir = path.join(outputDir, 'assets')
fs.rmSync(assetsDir, { recursive: true, force: true })
fs.writeFileSync(
  path.join(outputDir, 'README.md'),
  '# HTML 审查版\n\n直接双击 `index.html` 即可打开。该文件内联了 Vue 构建后的 CSS 和 JavaScript；绘本图片与音频位于 `demo/`，使用相对路径加载。\n\n重新生成请运行：\n\n```powershell\nnpm run build:html-review\n```\n',
  'utf8',
)
console.log('Inlined HTML review assets into', htmlPath)
