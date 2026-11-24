// writes must carry Content-Type or the server rejects them
// a string body is sent as-is (import forwards the file untouched)
export async function api<T = { ok: true }>(method: string, path: string, body?: unknown): Promise<T> {
  const opts: RequestInit = { method }
  if (method !== 'GET') {
    opts.headers = { 'Content-Type': 'application/json' }
    if (body !== undefined) opts.body = typeof body === 'string' ? body : JSON.stringify(body)
  }
  const res = await fetch('/api' + path, opts)
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`)
  return data
}

export const message = (e: unknown) => (e instanceof Error ? e.message : String(e))
