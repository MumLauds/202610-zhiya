export function countReadPages(session) {
  const visited = session.visitedPageNumbers ?? []
  const startPage = session.startPageNumber ?? (session.carriedPrefixPageCount ?? 0) + 1
  return startPage > 1 ? visited.filter((page) => page >= startPage).length : visited.length
}

export function canContinueStory(session, version) {
  return session?.mode === 'PARENT_CHILD' && session.status === 'IN_PROGRESS'
    && session.sourceType === 'ORIGINAL' && ['APPROVED_PRIVATE', 'RETIRED'].includes(version?.status)
}
