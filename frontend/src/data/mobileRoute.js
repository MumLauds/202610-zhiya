const tabs = ['home', 'library', 'original', 'growth', 'profile', 'shelf']

export function parseMobileRoute(hash = '') {
  const tab = hash.slice(1)
  if (hash.startsWith('#') && tabs.includes(tab)) return { name: tab, hash }
  const match = /^#(book|reader)\/([1-9]\d*)$/.exec(hash)
  if (match && Number.isSafeInteger(Number(match[2]))) {
    return { name: match[1], id: Number(match[2]), hash }
  }
  return { name: 'home', hash: '' }
}
