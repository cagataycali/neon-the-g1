import { useEffect, useState } from 'react'
import { fetchStatus, register, login, getToken, clearToken, type AuthStatus } from '../lib/auth'

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [authed, setAuthed] = useState<boolean>(!!getToken())
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string>('')
  const [bootstrap, setBootstrap] = useState('')

  const refresh = () => fetchStatus().then(setStatus).catch(() => setStatus({ enabled: true } as AuthStatus))

  useEffect(() => {
    refresh()
    const onExpired = () => { setAuthed(false); refresh() }
    window.addEventListener('neon-auth-expired', onExpired)
    return () => window.removeEventListener('neon-auth-expired', onExpired)
  }, [])

  // auth disabled entirely → pass through
  if (status && status.enabled === false) return <>{children}</>
  // already have a session token → render app (server 401 will bounce back)
  if (authed && status && !status.setup_required) return <>{children}</>

  const doRegister = async () => {
    setBusy(true); setErr('')
    try { await register('admin passkey', bootstrap); setAuthed(true) }
    catch (e: any) { setErr(e.message || String(e)) }
    finally { setBusy(false) }
  }
  const doLogin = async () => {
    setBusy(true); setErr('')
    try { await login(); setAuthed(true) }
    catch (e: any) { setErr(e.message || String(e)) }
    finally { setBusy(false) }
  }

  const setup = status?.setup_required
  const insecure = status && status.secure_context === false
  const badRp = status && status.rpid_usable === false

  return (
    <div className="gate">
      <div className="gate-card">
        <div className="gate-mark">N</div>
        <h1 className="gate-title">NEON<span>·</span>G1</h1>
        <p className="gate-sub">
          {setup ? 'Seal this robot with a passkey' : 'Unlock with your passkey'}
        </p>

        {(insecure || badRp) && (
          <div className="gate-warn">{status?.warning || 'This origin can’t run passkeys. Open over HTTPS with a hostname.'}</div>
        )}
        {err && <div className="gate-err">{err}</div>}

        {setup ? (
          <>
            {status?.bootstrap_required && (
              <input className="gate-input" type="password" placeholder="bootstrap token"
                value={bootstrap} onChange={(e) => setBootstrap(e.target.value)} />
            )}
            <button className="gate-btn primary" onClick={doRegister} disabled={busy || !!insecure || !!badRp}>
              {busy ? 'creating…' : '🔑 Create admin passkey'}
            </button>
            <p className="gate-note">
              The private key never leaves your device (Touch ID / Face ID / security key).
              After this, the whole interface is sealed.
            </p>
          </>
        ) : (
          <>
            <button className="gate-btn primary" onClick={doLogin} disabled={busy || !!insecure || !!badRp}>
              {busy ? 'verifying…' : '🔓 Unlock with passkey'}
            </button>
            <button className="gate-btn ghost" onClick={() => { clearToken(); refresh() }}>
              use a different device
            </button>
          </>
        )}
        <div className="gate-foot">
          {status?.rp_id && <span>rp: {status.rp_id}</span>}
        </div>
      </div>
    </div>
  )
}
