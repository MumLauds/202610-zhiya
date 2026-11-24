const stack = []
const bindings = new WeakMap()
const focusSelector = 'a[href], button, input:not([type="hidden"]), select, textarea, summary, [tabindex], [contenteditable="true"]'

function tabbable(root) {
  return [...root.querySelectorAll(focusSelector)].filter(element => {
    if (element.tabIndex < 0 || element.matches(':disabled') || element.closest('[hidden], [inert], [aria-hidden="true"]')) return false
    const style = window.getComputedStyle(element)
    return style.display !== 'none' && style.visibility !== 'hidden'
  })
}

export const vModalFocus = {
  mounted(root, { value }) {
    const opener = document.activeElement
    const modal = { root, close: value }
    bindings.set(root, modal)
    stack.push(modal)
    root.tabIndex = -1
    const focusInside = (last = false) => {
      const options = tabbable(root)
      const target = options[last ? options.length - 1 : 0] || root
      target.focus()
    }
    modal.onFocus = (event) => {
      if (stack.at(-1) === modal && !root.contains(event.target)) focusInside()
    }
    modal.onKey = (event) => {
      if (stack.at(-1) !== modal) return
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        modal.close()
        return
      }
      if (event.key !== 'Tab') return
      const options = tabbable(root)
      const index = options.indexOf(document.activeElement)
      if (index < 0 || index === (event.shiftKey ? 0 : options.length - 1)) {
        event.preventDefault()
        focusInside(event.shiftKey)
      }
    }
    modal.opener = opener
    document.addEventListener('keydown', modal.onKey, true)
    document.addEventListener('focusin', modal.onFocus)
    queueMicrotask(() => {
      if (root.isConnected && stack.at(-1) === modal && !root.contains(document.activeElement)) focusInside()
    })
  },
  updated(root, { value }) {
    bindings.get(root).close = value
  },
  beforeUnmount(root) {
    const modal = bindings.get(root)
    const wasOnTop = stack.at(-1) === modal
    stack.splice(stack.indexOf(modal), 1)
    document.removeEventListener('keydown', modal.onKey, true)
    document.removeEventListener('focusin', modal.onFocus)
    bindings.delete(root)
    if (wasOnTop && modal.opener?.isConnected) modal.opener.focus()
  },
}
