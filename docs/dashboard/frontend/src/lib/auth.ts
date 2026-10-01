// WebAuthn passkey auth — token store + fetch wrapper + ceremonies.
const KEY = 'neon_session'

export function getToken(): string { return localStorage.getItem(KEY) || '' }
export function setToken(t: string) { localStorage.setItem(KEY, t) }
export function clearToken() { localStorage.removeItem(KEY) }

// fetch that attaches the bearer token + 401-redirects to login
export async function authedFetch(url: string, opts: RequestInit = {}): Promise<Response> {
  const t = getToken()
  const headers = new Headers(opts.headers || {})
  if (t) headers.set('Authorization', `Bearer ${t}`)
  const r = await fetch(url, { ...opts, headers })
  if (r.status === 401) { clearToken(); window.dispatchEvent(new Event('neon-auth-expired')) }
  return r
}

// base64url helpers for WebAuthn <-> JSON
const b64uToBuf = (s: string): ArrayBuffer => {
  const pad = '='.repeat((4 - (s.length % 4)) % 4)
  const b = atob((s + pad).replace(/-/g, '+').replace(/_/g, '/'))
  const arr = new Uint8Array(b.length)
  for (let i = 0; i < b.length; i++) arr[i] = b.charCodeAt(i)
  return arr.buffer
}
const bufToB64u = (buf: ArrayBuffer): string => {
  const bytes = new Uint8Array(buf)
  let s = ''
  for (const b of bytes) s += String.fromCharCode(b)
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

export interface AuthStatus {
  enabled: boolean; setup_required: boolean; bootstrap_required?: boolean
  credentials: { id: string; name: string; created: number }[]
  secure_context?: boolean; rpid_usable?: boolean; warning?: string; rp_id?: string
}

export async function fetchStatus(): Promise<AuthStatus> {
  const r = await fetch('/auth/status')
  return r.json()
}

// enroll a new passkey (first = admin). Returns session token.
export async function register(label = 'admin passkey', bootstrap = ''): Promise<string> {
  const begin = await authedFetch('/auth/register/begin', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ label, bootstrap }),
  })
  if (!begin.ok) throw new Error((await begin.json()).detail || 'register begin failed')
  const { challenge_id, options } = await begin.json()

  const publicKey: any = { ...options }
  publicKey.challenge = b64uToBuf(options.challenge)
  publicKey.user = { ...options.user, id: b64uToBuf(options.user.id) }
  if (options.excludeCredentials)
    publicKey.excludeCredentials = options.excludeCredentials.map((c: any) => ({ ...c, id: b64uToBuf(c.id) }))

  const cred: any = await navigator.credentials.create({ publicKey })
  const payload = {
    challenge_id,
    credential: {
      id: cred.id, rawId: bufToB64u(cred.rawId), type: cred.type,
      response: {
        clientDataJSON: bufToB64u(cred.response.clientDataJSON),
        attestationObject: bufToB64u(cred.response.attestationObject),
      },
    },
  }
  const fin = await authedFetch('/auth/register/finish', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!fin.ok) throw new Error((await fin.json()).detail || 'register finish failed')
  const { token } = await fin.json()
  setToken(token)
  return token
}

// login with an existing passkey. Returns session token.
export async function login(): Promise<string> {
  const begin = await fetch('/auth/login/begin', { method: 'POST' })
  if (!begin.ok) throw new Error((await begin.json()).detail || 'login begin failed')
  const { challenge_id, options } = await begin.json()

  const publicKey: any = { ...options }
  publicKey.challenge = b64uToBuf(options.challenge)
  if (options.allowCredentials)
    publicKey.allowCredentials = options.allowCredentials.map((c: any) => ({ ...c, id: b64uToBuf(c.id) }))

  const cred: any = await navigator.credentials.get({ publicKey })
  const payload = {
    challenge_id,
    credential: {
      id: cred.id, rawId: bufToB64u(cred.rawId), type: cred.type,
      response: {
        clientDataJSON: bufToB64u(cred.response.clientDataJSON),
        authenticatorData: bufToB64u(cred.response.authenticatorData),
        signature: bufToB64u(cred.response.signature),
        userHandle: cred.response.userHandle ? bufToB64u(cred.response.userHandle) : null,
      },
    },
  }
  const fin = await fetch('/auth/login/finish', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!fin.ok) throw new Error((await fin.json()).detail || 'login finish failed')
  const { token } = await fin.json()
  setToken(token)
  return token
}
