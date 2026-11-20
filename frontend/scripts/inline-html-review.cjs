const fs = require('fs')
const path = require('path')

const root = path.resolve(__dirname, '..')
const outputDir = path.join(root, 'html-review')
const mobileHtmlPath = path.join(outputDir, 'mobile.html')
const htmlPath = path.join(outputDir, 'index.html')
let html = fs.readFileSync(mobileHtmlPath, 'utf8')

html = html.replace(/<link rel="stylesheet"[^>]*href="\.\/([^"]+\.css)"[^>]*>/g, (_match, file) => {
  const css = fs.readFileSync(path.join(outputDir, file), 'utf8')
  return `<style>\n${css}\n</style>`
})

html = html.replace(/<script type="module"[^>]*src="\.\/([^"]+\.js)"[^>]*><\/script>/g, (_match, file) => {
  const js = fs.readFileSync(path.join(outputDir, file), 'utf8').replace(/<\/script/gi, '<\\/script')
  return `<script type="module">\n${js}\n</script>`
})

fs.writeFileSync(htmlPath, html, 'utf8')
fs.unlinkSync(mobileHtmlPath)

const assetsDir = path.join(outputDir, 'assets')
fs.rmSync(assetsDir, { recursive: true, force: true })
fs.writeFileSync(
  path.join(outputDir, 'README.md'),
  '# HTML 审查版\n\n直接双击 `index.html` 即可打开移动端审查版。该文件内联了 Vue 构建后的 CSS 和 JavaScript，绘本图片与音频从 `../public/demo/` 相对加载。\n\n重新生成请运行：\n\n```powershell\nnpm run build:html-review\n```\n',
  'utf8',
)
console.log('Inlined HTML review assets into', htmlPath)
