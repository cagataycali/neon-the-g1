import { useEffect, useState } from 'react'
import { authedFetch, clearToken } from '../lib/auth'
import { Ico } from './Icons'
import { Drawer } from '../App'
import { useScheme, type Scheme } from '../lib/scheme'

type Tab = 'model' | 'wifi' | 'env' | 'passkeys'

export default function ConfigPanel({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('model')
  return (
    <Drawer title="Configuration" icon={Ico.sliders()} onClose={onClose} className="config">
      <SchemeRow />
      <div className="config-tabs" role="tablist" aria-label="Configuration section">
        {(['model', 'wifi', 'env', 'passkeys'] as Tab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? 'ctab on' : 'ctab'} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      <div className="config-body">
        {tab === 'model' && <ModelTab />}
        {tab === 'wifi' && <WifiTab />}
        {tab === 'env' && <EnvTab />}
        {tab === 'passkeys' && <PasskeyTab />}
      </div>
      <button className="gate-btn ghost signout" onClick={() => { clearToken(); location.reload() }}>Sign out</button>
    </Drawer>
  )
}

/** Scheme: auto (follow the OS) / paper / dark, persisted in localStorage "neon-scheme" by lib/scheme.ts. */
function SchemeRow() {
  const [scheme, setScheme] = useScheme()
  return (
    <div className="scheme-row" role="radiogroup" aria-label="Colour scheme">
      <span className="cfg-label" id="scheme-label">Scheme</span>
      {(['auto', 'paper', 'dark'] as Scheme[]).map(s => (
        <button key={s} role="radio" aria-checked={scheme === s} className={scheme === s ? 'cfg-chip on' : 'cfg-chip'} onClick={() => setScheme(s)}>
          {s === 'paper' ? <span className="ic">{Ico.sun()}</span> : s === 'dark' ? <span className="ic">{Ico.moon()}</span> : null}{s}
        </button>
      ))}
    </div>
  )
}

type ModelState = { configured: string; live: string | null; boot: string; pending: string[]; known: string[]; ctl: { alive: boolean; age_s: number | null } }

/** Model: Live (what the dashboard agent answers with) vs Configured (.env, what a
 *  recreated persona will run). Save applies to the dashboard at once; the other
 *  personas need a container recreate through the host's neon-ctl ("Apply to all"). */
function ModelTab() {
  const [st, setSt] = useState<ModelState | null>(null)
  const [val, setVal] = useState(''); const [msg, setMsg] = useState(''); const [warn, setWarn] = useState(false)
  const [busy, setBusy] = useState<string>('')
  const load = () => authedFetch('/api/config/model').then(r => r.json()).then((d: ModelState) => { setSt(d); setVal(v => v || d.configured) }).catch(() => {})
  useEffect(() => { load() }, [])
  const say = (m: string, w = false) => { setMsg(m); setWarn(w) }
  const save = async () => {
    say('saving')
    const r = await authedFetch('/api/config/model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model_id: val }) })
    const d = await r.json()
    if (!d.ok) { say(d.error || 'failed', true); return }
    say(`saved; dashboard chat uses it on the next message, ${d.pending.length} personas pending`)
    load()
  }
  /** Recreate the pending containers and wait for the dashboard to come back on the new model. */
  const applyAll = async () => {
    if (!st) return
    const services = st.pending.length ? st.pending : ['neon-agent', 'neon-telegram', 'neon-thinker', 'neon-dashboard']
    const t0 = Date.now(); const tick = () => Math.round((Date.now() - t0) / 1000)
    setBusy(`recreating ${services.join(', ')}`)
    say('')
    let r: Response
    try {
      r = await authedFetch('/api/config/service/restart', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ services }) })
    } catch { r = new Response(JSON.stringify({ ok: true, pending: true, self_restart: true }), { status: 202 }) }
    const d = await r.json().catch(() => ({ ok: false, error: 'bad answer' }))
    if (!d.ok) { setBusy(''); say(d.error || 'failed', true); return }
    if (!d.self_restart) { setBusy(''); say(`recreated ${services.join(', ')} in ${tick()} s`); load(); return }
    // the dashboard itself restarts: poll until it answers and reports nothing pending
    let sawDown = false
    for (let i = 0; i < 90; i++) {
      await new Promise(res => setTimeout(res, 2000))
      setBusy(`${sawDown ? 'waiting for the dashboard' : 'recreating'} ${services.length} containers (${tick()} s)`)
      try {
        const h = await fetch('/api/health', { cache: 'no-store' })
        if (!h.ok) { sawDown = true; continue }
        if (!sawDown && tick() < 6) continue
        const m: ModelState = await authedFetch('/api/config/model').then(x => x.json())
        setSt(m)
        if (m.pending.length === 0) { setBusy(''); say(`live on ${m.live || m.boot} after ${tick()} s`); return }
      } catch { sawDown = true }
    }
    setBusy(''); say('still not back after 180 s; check docker ps on the Jetson', true)
  }
  const live = st?.live || st?.boot || ''
  const differs = !!st && st.configured !== live
  const pending = st?.pending || []
  return (
    <div className="cfg-section" aria-busy={!!busy}>
      <div className="cfg-label">Live model <span className="cfg-note-inline">dashboard chat</span></div>
      <div className="cfg-current mono">{live || '--'}</div>
      {differs && (<>
        <div className="cfg-label">Configured <span className="cfg-note-inline">.env</span></div>
        <div className="cfg-current mono">{st!.configured}</div>
      </>)}
      {pending.length > 0 && (
        <div className="badges"><span className="badge warn"><span className="ic">{Ico.alert()}</span>restart pending</span>
          {pending.map(p => <span key={p} className="badge mono">{p.replace('neon-', '')}</span>)}</div>
      )}
      {st && !st.ctl.alive && <div className="badges"><span className="badge warn"><span className="ic">{Ico.alert()}</span>neon-ctl offline on the host</span></div>}
      <div className="cfg-label">Change to</div>
      <div className="cfg-chips">
        {(st?.known || []).map(m => <button key={m} className={val === m ? 'cfg-chip on' : 'cfg-chip'} onClick={() => setVal(m)}>{m.split('.').pop()}</button>)}
      </div>
      <input className="gate-input mono" value={val} onChange={e => setVal(e.target.value)} placeholder="model id" aria-label="Model id" />
      <div className="cfg-row">
        <button className="gate-btn primary" onClick={save} disabled={!!busy || !val || val === st?.configured}>Save model</button>
        <button className="gate-btn" onClick={applyAll} disabled={!!busy || !st || !st.ctl.alive} title="docker compose up -d --force-recreate on the host">
          <span className="ic">{Ico.refresh()}</span>{pending.length ? `Apply to ${pending.length} personas` : 'Recreate all'}
        </button>
      </div>
      {busy && <div className="cfg-msg progress mono" role="status"><span className="spin" aria-hidden="true" />{busy}</div>}
      {msg && <div className={warn ? 'cfg-msg warn' : 'cfg-msg'} role="status">{msg}</div>}
      <div className="cfg-note">Save writes NEON_MODEL_ID to .env and switches the dashboard agent at once. Apply recreates the listed containers (about 20 s); the dashboard reconnects by itself.</div>
    </div>
  )
}

function WifiTab() {
  const [nets, setNets] = useState<any[]>([]); const [active, setActive] = useState<any>(null)
  const [ssid, setSsid] = useState(''); const [pw, setPw] = useState(''); const [msg, setMsg] = useState(''); const [scanning, setScanning] = useState(false)
  const status = () => authedFetch('/api/config/wifi/status').then(r => r.json()).then(d => setActive(d.active))
  const scan = async () => { setScanning(true); const r = await authedFetch('/api/config/wifi/scan'); const d = await r.json(); setNets(d.networks || []); setScanning(false) }
  useEffect(() => { status(); scan() }, [])
  const connect = async () => {
    setMsg('connecting')
    const r = await authedFetch('/api/config/wifi/connect', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ssid, password: pw }) })
    const d = await r.json(); setMsg(d.ok ? `connected to ${ssid}` : d.message || d.error || 'failed'); if (d.ok) { setPw(''); status() }
  }
  return (
    <div className="cfg-section">
      <div className="cfg-label">Companion WiFi {active && <span className="txt-green">{active.ssid}</span>}</div>
      <div className="wifi-list">
        {nets.map(n => (
          <button key={n.ssid} className={ssid === n.ssid ? 'wifi-row on' : 'wifi-row'} onClick={() => setSsid(n.ssid)}>
            <span className="wifi-ssid">{n.active && <span className="dot open" role="img" aria-label="connected" />}{n.ssid}</span>
            <span className="wifi-meta">{n.security !== 'open' && <span className="ic" role="img" aria-label="secured">{Ico.lock()}</span>}{n.signal}%</span>
          </button>
        ))}
        {nets.length === 0 && <div className="empty">{scanning ? 'scanning' : 'no networks'}</div>}
      </div>
      <input className="gate-input" placeholder="network name (SSID)" aria-label="Network name" value={ssid} onChange={e => setSsid(e.target.value)} />
      <input className="gate-input" type="password" placeholder="password" aria-label="WiFi password" value={pw} onChange={e => setPw(e.target.value)} />
      <div className="cfg-row">
        <button className="gate-btn" onClick={scan} disabled={scanning}><span className="ic">{Ico.refresh()}</span>{scanning ? 'scanning' : 'scan'}</button>
        <button className="gate-btn primary" onClick={connect} disabled={!ssid}>Connect</button>
      </div>
      {msg && <div className="cfg-msg">{msg}</div>}
      <div className="cfg-note">Changes the Jetson's WiFi (wlan0). The robot DDS link (eth0) is untouched.</div>
    </div>
  )
}

type TokenHealth = { state: 'ok' | 'expired' | 'missing' | 'invalid'; exp: number | null; clock_synced?: boolean }

/** NEON_CAMERA_PROXY_TOKEN health. The Jetson boots at a 1970 clock until NTP; a
 *  token minted then is dated 1980 and the telegram/thinker personas get 401 from
 *  the camera proxy. Refresh mints on the host (neon-ctl token-refresh) and
 *  recreates the two consumers. */
function TokenRow({ onDone }: { onDone: () => void }) {
  const [tok, setTok] = useState<TokenHealth | null>(null); const [busy, setBusy] = useState(false); const [msg, setMsg] = useState(''); const [warn, setWarn] = useState(false)
  const load = () => authedFetch('/api/config/token').then(r => r.json()).then(setTok).catch(() => setTok(null))
  useEffect(() => { load() }, [])
  const refresh = async () => {
    setBusy(true); setMsg('minting on the host, recreating telegram + thinker'); setWarn(false)
    try {
      const r = await authedFetch('/api/config/token/refresh', { method: 'POST' })
      const d = await r.json()
      if (d.ok) { setMsg('token refreshed; telegram + thinker recreated'); onDone() } else { setMsg(d.error || 'refresh failed'); setWarn(true) }
    } catch { setMsg('refresh failed'); setWarn(true) }
    setBusy(false); load()
  }
  if (!tok) return null
  const ok = tok.state === 'ok'
  const expTxt = tok.exp ? new Date(tok.exp * 1000).toISOString().slice(0, 10) : ''
  return (
    <div className="token-row">
      <span className="cfg-label">Camera proxy token</span>
      <span className={ok ? 'badge ok mono' : 'badge warn mono'}>
        <span className="ic">{ok ? Ico.check() : Ico.alert()}</span>
        {tok.state}{expTxt ? ` exp ${expTxt}` : ''}
      </span>
      {!ok && <span className="cfg-note-inline">telegram and thinker cannot read the cameras</span>}
      <button className={ok ? 'gate-btn ghost' : 'gate-btn primary'} onClick={refresh} disabled={busy || tok.clock_synced === false}>
        {busy ? <span className="spin" aria-hidden /> : null}Refresh token
      </button>
      {tok.clock_synced === false && <span className="cfg-note-inline">clock not synced yet</span>}
      {msg && <div className={warn ? 'cfg-msg warn' : busy ? 'cfg-msg progress' : 'cfg-msg'} role="status">{msg}</div>}
    </div>
  )
}

function EnvTab() {
  const [vars, setVars] = useState<any[]>([]); const [path, setPath] = useState(''); const [edits, setEdits] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState(''); const [example, setExample] = useState('')
  const load = () => authedFetch('/api/config/env').then(r => r.json()).then(d => { setVars(d.vars || []); setPath(d.path) })
  useEffect(() => { load(); authedFetch('/api/config/env/example').then(r => r.json()).then(d => setExample(d.text || '')) }, [])
  const save = async () => {
    if (Object.keys(edits).length === 0) { setMsg('no changes'); return }
    setMsg('saving')
    const r = await authedFetch('/api/config/env', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ updates: edits }) })
    const d = await r.json(); setMsg(d.ok ? `saved ${d.updated?.length || 0} keys` : 'failed'); setEdits({}); load()
  }
  return (
    <div className="cfg-section">
      <div className="cfg-label">Environment <span className="cfg-note-inline mono">{path}</span></div>
      <TokenRow onDone={load} />
      <div className="env-list">
        {vars.map(v => (
          <div className="env-row" key={v.key}>
            <label className="env-key mono" htmlFor={`env-${v.key}`}>{v.key}{v.secret && <span className="ic" role="img" aria-label="secret">{Ico.lock()}</span>}</label>
            <input id={`env-${v.key}`} className="gate-input mono env-val" defaultValue={edits[v.key] ?? (v.secret ? '' : v.value)}
              placeholder={v.secret ? (v.set ? v.value : 'unset') : 'unset'}
              onChange={e => setEdits(p => ({ ...p, [v.key]: e.target.value }))} />
          </div>
        ))}
      </div>
      <button className="gate-btn primary" onClick={save}>Save .env</button>
      {msg && <div className="cfg-msg">{msg}</div>}
      <details className="cfg-details"><summary>.env.example</summary><div className="term"><div className="term-head">.env.example</div><pre className="code mono">{example}</pre></div></details>
    </div>
  )
}

function PasskeyTab() {
  const [creds, setCreds] = useState<any[]>([]); const [msg, setMsg] = useState('')
  const load = () => authedFetch('/api/auth/credentials').then(r => r.json()).then(d => setCreds(d.credentials || []))
  useEffect(() => { load() }, [])
  const add = async () => {
    setMsg('creating')
    try { const { register } = await import('../lib/auth'); await register('passkey'); setMsg('added'); load() }
    catch (e: any) { setMsg(e.message || 'failed') }
  }
  const del = async (id: string) => {
    const r = await authedFetch(`/api/auth/credentials/${encodeURIComponent(id)}`, { method: 'DELETE' })
    const d = await r.json(); setMsg(d.ok ? 'removed' : d.detail || 'failed'); load()
  }
  return (
    <div className="cfg-section">
      <div className="cfg-label">Enrolled passkeys</div>
      <div className="wifi-list">
        {creds.map(c => (
          <div className="env-row" key={c.id}>
            <span className="wifi-ssid"><span className="ic">{Ico.key()}</span>{c.name}</span>
            <button className="icon-btn sm" onClick={() => del(c.id)} aria-label={`Revoke passkey ${c.name}`} title="Revoke">{Ico.close()}</button>
          </div>
        ))}
        {creds.length === 0 && <div className="empty">none</div>}
      </div>
      <button className="gate-btn primary" onClick={add}><span className="ic">{Ico.plus()}</span>Add passkey</button>
      {msg && <div className="cfg-msg">{msg}</div>}
      <div className="cfg-note">Add a passkey on another device to share operator access. The last passkey cannot be removed.</div>
    </div>
  )
}
