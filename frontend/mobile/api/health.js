export async function checkHealth() {
  const response = await fetch('/api/v1/health', {
    cache: 'no-store',
    signal: AbortSignal.timeout(5000),
  })
  if (!response.ok) return false
  const result = await response.json()
  return result.data?.status === 'ok' && Boolean(result.requestId)
}
