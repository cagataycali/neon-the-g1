import { useEffect, useState } from 'react'
import { fetchStatus, register, login, getToken, clearToken, type AuthStatus } from '../lib/auth'
import { Brand } from './Brand'
import { Ico } from './Icons'

/** The Gate: the first thing a team mate sees. Register / login / clearToken semantics are unchanged. */
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

  // auth disabled entirely -> pass through
  if (status && status.enabled === false) return <>{children}</>
  // already have a session token -> render app (server 401 will bounce back)
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
  const blocked = busy || !!insecure || !!badRp

  return (
    <div className="gate" data-testid="auth-gate">
      <div className="gate-card">
        <Brand />
        <h1 className="gate-title">{setup ? 'Seal this robot with a passkey' : 'Sign in to the G1 cockpit'}</h1>
        <p className="gate-sub">The Unitree G1 cockpit: cameras, lidar, telemetry and the agent.</p>

        {(insecure || badRp) && (
          <div className="gate-warn" role="alert"><span className="ic">{Ico.alert()}</span><span>{status?.warning || 'This origin cannot run passkeys. Open over HTTPS with a hostname.'}</span></div>
        )}
        {err && <div className="gate-err" role="alert"><span className="ic">{Ico.alert()}</span><span>{err}</span></div>}

        {setup ? (
          <>
            {status?.bootstrap_required && (
              <>
                <label className="lbl" htmlFor="gate-bootstrap">Bootstrap token</label>
                <input id="gate-bootstrap" className="gate-input mono" type="password" placeholder="from NEON_AUTH_BOOTSTRAP_TOKEN in .env" autoComplete="off"
                  value={bootstrap} onChange={(e) => setBootstrap(e.target.value)} />
              </>
            )}
            <button className="gate-btn primary" onClick={doRegister} disabled={blocked} aria-busy={busy}>
              <span className="ic">{Ico.key()}</span>{busy ? 'creating' : 'Create the admin passkey'}
            </button>
            <p className="gate-note">
              The private key never leaves your device (Touch ID, Face ID or a security key).
              After this, the whole interface is sealed; more passkeys are added in Configuration.
            </p>
          </>
        ) : (
          <>
            <button className="gate-btn primary" onClick={doLogin} disabled={blocked} aria-busy={busy}>
              <span className="ic">{Ico.key()}</span>{busy ? 'verifying' : 'Continue with passkey'}
            </button>
            <button className="gate-btn ghost" onClick={() => { clearToken(); refresh() }}>
              use a different device
            </button>
          </>
        )}
        <div className="gate-foot">
          <span>{status?.rp_id ? `rp ${status.rp_id}` : ''}</span>
          <span>passkeys only, no passwords</span>
        </div>
      </div>
    </div>
  )
}
