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

function ModelTab() {
  const [cur, setCur] = useState(''); const [known, setKnown] = useState<string[]>([])
  const [val, setVal] = useState(''); const [msg, setMsg] = useState('')
  useEffect(() => { authedFetch('/api/config/model').then(r => r.json()).then(d => { setCur(d.current); setVal(d.current); setKnown(d.known || []) }) }, [])
  const save = async () => {
    setMsg('saving')
    const r = await authedFetch('/api/config/model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model_id: val }) })
    const d = await r.json(); setMsg(d.ok ? 'saved, restart to apply' : d.error || 'failed'); if (d.ok) setCur(val)
  }
  return (
    <div className="cfg-section">
      <div className="cfg-label">Active model</div>
      <div className="cfg-current mono">{cur || '--'}</div>
      <div className="cfg-label">Change to</div>
      <div className="cfg-chips">
        {known.map(m => <button key={m} className={val === m ? 'cfg-chip on' : 'cfg-chip'} onClick={() => setVal(m)}>{m.split('.').pop()}</button>)}
      </div>
      <input className="gate-input mono" value={val} onChange={e => setVal(e.target.value)} placeholder="model id" aria-label="Model id" />
      <button className="gate-btn primary" onClick={save}>Save model</button>
      {msg && <div className="cfg-msg">{msg}</div>}
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
