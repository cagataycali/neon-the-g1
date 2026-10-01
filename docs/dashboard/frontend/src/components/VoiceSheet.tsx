import { useCallback, useEffect, useState } from 'react'
import { authedFetch } from '../lib/auth'
import { Ico } from './Icons'
import { Drawer } from '../App'

/** GET /api/voice/status */
export type VoiceStatus = {
  muted: boolean; muted_until: number | null; remaining_s: number
  provider: string; voice: string; model: string
  service_active: string | null; ctl_alive: boolean
  snooze_options: number[]
  catalog: { openai: string[]; nova_sonic: string[]; gemini: string[]; openai_models: string[] }
}

const fmtRemaining = (s: number) => {
  if (s <= 0) return ''
  if (s < 3600) return `${Math.max(1, Math.ceil(s / 60))}m`
  const h = Math.floor(s / 3600); const m = Math.round((s % 3600) / 60)
  return m ? `${h}h${String(m).padStart(2, '0')}` : `${h}h`
}

/** Polls the voice state every 5 s (the kv the listener's MuteFlag reads). */
export function useVoiceStatus(intervalMs = 5000) {
  const [st, setSt] = useState<VoiceStatus | null>(null)
  const refresh = useCallback(() => authedFetch('/api/voice/status').then(r => r.ok ? r.json() : null).then(d => { if (d) setSt(d) }).catch(() => {}), [])
  useEffect(() => { refresh(); const id = setInterval(refresh, intervalMs); return () => clearInterval(id) }, [refresh, intervalMs])
  // count the remaining seconds down between polls so the pill does not jump
  useEffect(() => {
    if (!st?.muted || !st.remaining_s) return
    const id = setInterval(() => setSt(s => s && s.remaining_s > 0 ? { ...s, remaining_s: s.remaining_s - 1 } : s), 1000)
    return () => clearInterval(id)
  }, [st?.muted, st?.muted_until])
  return { st, refresh }
}

/** Topbar pill: filled green while the voice is live, warn outline + "SNOOZED 42m" while muted. */
export function VoicePill({ st, onClick }: { st: VoiceStatus | null; onClick: () => void }) {
  const muted = !!st?.muted
  const label = !st ? 'VOICE' : muted ? (st.remaining_s ? `SNOOZED ${fmtRemaining(st.remaining_s)}` : 'MUTED') : 'VOICE'
  const title = !st ? 'Voice' : muted ? `Voice muted${st.remaining_s ? `, back in ${fmtRemaining(st.remaining_s)}` : ' until unmuted'}` : `Voice live: ${st.voice} / ${st.model}`
  return (
    <button className={`mic-pill ${!st ? 'unknown' : muted ? 'muted' : 'live'}`} onClick={onClick} aria-label={title} title={title}
      aria-pressed={muted} data-testid="voice-pill">
      <span className="ic">{muted ? Ico.micOff() : Ico.mic()}</span><span className="mono">{label}</span>
    </button>
  )
}

/** Voice sheet: snooze / unmute, and the voice profile (restarts the listener through neon-ctl). */
export default function VoiceSheet({ st, refresh, onClose }: { st: VoiceStatus | null; refresh: () => Promise<void> | void; onClose: () => void }) {
  const [busy, setBusy] = useState(''); const [msg, setMsg] = useState(''); const [warn, setWarn] = useState(false)
  const [provider, setProvider] = useState(''); const [voice, setVoice] = useState(''); const [model, setModel] = useState('')
  useEffect(() => { if (st && !provider) { setProvider(st.provider); setVoice(st.voice); setModel(st.model) } }, [st, provider])
  const dev = typeof location !== 'undefined' && location.search.includes('dev')
  const say = (m: string, w = false) => { setMsg(m); setWarn(w) }
  const post = async (path: string, body?: any) => {
    const r = await authedFetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
    return r.json().catch(() => ({ ok: false, error: `HTTP ${r.status}` }))
  }
  const mute = async (minutes: number | null) => {
    setBusy('muting'); const d = await post('/api/voice/mute', { minutes }); setBusy('')
    if (!d.ok) { say(d.error || 'failed', true); return }
    say(minutes ? `snoozed for ${fmtRemaining(minutes * 60)}` : 'muted until you unmute'); await refresh()
  }
  const unmute = async () => {
    setBusy('unmuting'); const d = await post('/api/voice/unmute'); setBusy('')
    if (!d.ok) { say(d.error || 'failed', true); return }
    say('voice live'); await refresh()
  }
  const apply = async () => {
    setBusy('writing .env and restarting neon-voice (about 5 s)')
    const d = await post('/api/voice/profile', { provider, voice, model, restart: true })
    setBusy('')
    if (!d.ok) { say(d.error || d.restart?.error || 'failed', true); return }
    say(`listener restarted: ${d.provider} / ${d.voice} / ${d.model}`); await refresh()
  }
  const voices: string[] = st ? ((st.catalog as any)[provider] || []) : []
  const dirty = !!st && (provider !== st.provider || voice !== st.voice || model !== st.model)
  const muted = !!st?.muted
  return (
    <Drawer title="Voice" icon={Ico.mic()} onClose={onClose} className="voice">
      <div className="cfg-section" aria-busy={!!busy}>
        <div className="cfg-label">State</div>
        <div className="badges">
          <span className={`badge ${muted ? 'warn' : 'ok'}`}><span className="ic">{muted ? Ico.micOff() : Ico.mic()}</span>
            {!st ? '--' : muted ? (st.remaining_s ? `snoozed, back in ${fmtRemaining(st.remaining_s)}` : 'muted until unmuted') : 'live'}</span>
          {st && <span className={`badge ${st.service_active === 'active' ? 'ok' : st.service_active ? 'warn' : ''} mono`}>listener {st.service_active || 'unknown'}</span>}
          {st && !st.ctl_alive && <span className="badge warn"><span className="ic">{Ico.alert()}</span>neon-ctl offline</span>}
        </div>
        <div className="cfg-note">Muted means silent: the mic is dropped and the speaker plays nothing. A snooze lifts itself; the listener keeps running.</div>
        <div className="cfg-label">Snooze</div>
        <div className="cfg-chips">
          {(st?.snooze_options || [15, 60, 180]).map(m => <button key={m} className="cfg-chip" disabled={!!busy} onClick={() => mute(m)}>{fmtRemaining(m * 60)}</button>)}
          {dev && <button className="cfg-chip" disabled={!!busy} onClick={() => mute(1)} title="dev: one minute">1m</button>}
          <button className="cfg-chip" disabled={!!busy} onClick={() => mute(null)}>until I unmute</button>
        </div>
        <button className={muted ? 'gate-btn primary' : 'gate-btn'} disabled={!!busy || !muted} onClick={unmute}><span className="ic">{Ico.mic()}</span>Unmute now</button>

        <div className="cfg-label">Voice profile</div>
        <div className="cfg-row">
          <select className="gate-input mono" aria-label="Provider" value={provider} onChange={e => { setProvider(e.target.value); setVoice('') }}>
            {['openai', 'nova_sonic', 'gemini'].map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          {voices.length > 0
            ? <select className="gate-input mono" aria-label="Voice" value={voice} onChange={e => setVoice(e.target.value)}>
                {!voices.includes(voice) && <option value="">pick a voice</option>}
                {voices.map(v => <option key={v} value={v}>{v}</option>)}
              </select>
            : <input className="gate-input mono" aria-label="Voice" value={voice} onChange={e => setVoice(e.target.value)} placeholder="voice" />}
        </div>
        {provider === 'openai' && st && st.catalog.openai_models.length > 0 && (
          <div className="cfg-chips">{st.catalog.openai_models.map(m => <button key={m} className={model === m ? 'cfg-chip on' : 'cfg-chip'} onClick={() => setModel(m)}>{m}</button>)}</div>
        )}
        <input className="gate-input mono" aria-label="Voice model" value={model} onChange={e => setModel(e.target.value)} placeholder="model" />
        <button className="gate-btn primary" disabled={!!busy || !dirty || !voice || !st?.ctl_alive} onClick={apply} title="writes VOICE_* to .env, sudo systemctl restart neon-voice via neon-ctl">
          <span className="ic">{Ico.refresh()}</span>Apply and restart listener
        </button>
        {busy && <div className="cfg-msg progress mono" role="status"><span className="spin" aria-hidden="true" />{busy}</div>}
        {msg && <div className={warn ? 'cfg-msg warn' : 'cfg-msg'} role="status">{msg}</div>}
        {st && <div className="cfg-note mono">now: {st.provider} / {st.voice} / {st.model}</div>}
      </div>
    </Drawer>
  )
}
