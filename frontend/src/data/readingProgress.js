export function countReadPages(session) {
  const visited = session.visitedPageNumbers ?? []
  const startPage = session.startPageNumber ?? (session.carriedPrefixPageCount ?? 0) + 1
  return startPage > 1 ? visited.filter((page) => page >= startPage).length : visited.length
}
