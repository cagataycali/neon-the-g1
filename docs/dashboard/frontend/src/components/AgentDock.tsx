import { useEffect, useRef, useState } from 'react'
import { authedFetch } from '../lib/auth'
import { Ico } from './Icons'

interface Msg { id: number; role: 'user' | 'assistant' | 'error'; text: string; tool?: string; reasoning?: string }
let _id = 0

export default function AgentDock() {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [ready, setReady] = useState<boolean | null>(null)
  const [tools, setTools] = useState(0)
  const endRef = useRef<HTMLDivElement>(null)
  const streamRef = useRef<HTMLDivElement>(null)
  // Follow the stream only while the reader is at the bottom; scrolling up to
  // read earlier turns must never be undone by the next token.
  const [stuck, setStuck] = useState(true)
  const onScroll = () => {
    const el = streamRef.current; if (!el) return
    setStuck(el.scrollHeight - el.scrollTop - el.clientHeight < 24)
  }

  useEffect(() => {
    const poll = () => authedFetch('/api/chat/status').then(r => r.json())
      .then(d => { setReady(!!d.ready); setTools(d.tools || 0) }).catch(() => setReady(false))
    poll(); const id = setInterval(poll, 8000); return () => clearInterval(id)
  }, [])
  useEffect(() => { if (stuck) endRef.current?.scrollIntoView({ block: 'end' }) }, [msgs, busy, stuck])

  const send = async () => {
    const prompt = input.trim()
    if (!prompt || busy) return
    setInput('')
    setStuck(true)   // your own message always brings the view back down
    setMsgs(m => [...m, { id: ++_id, role: 'user', text: prompt }])
    setBusy(true)
    const aid = ++_id
    let acc = '', think = ''
    setMsgs(m => [...m, { id: aid, role: 'assistant', text: '' }])
    const patch = (fn: (m: Msg) => Msg) => setMsgs(m => m.map(x => x.id === aid ? fn(x) : x))
    try {
      const r = await authedFetch('/api/chat/stream', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prompt }),
      })
      if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`)
      const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = ''
      while (true) {
        const { done, value } = await reader.read(); if (done) break
        buf += dec.decode(value, { stream: true })
        const frames = buf.split('\n\n'); buf = frames.pop() || ''
        for (const f of frames) {
          const line = f.replace(/^data:\s*/, '').trim(); if (!line) continue
          let ev: any; try { ev = JSON.parse(line) } catch { continue }
          if (ev.type === 'text') { acc += ev.data; patch(m => ({ ...m, text: acc, tool: undefined })) }
          else if (ev.type === 'reasoning') { think += ev.data; patch(m => ({ ...m, reasoning: think })) }
          else if (ev.type === 'tool') { patch(m => ({ ...m, tool: ev.name })) }
          else if (ev.type === 'done') { patch(m => ({ ...m, text: (ev.reply || acc).trim(), tool: undefined })) }
          else if (ev.type === 'error') { patch(() => ({ id: aid, role: 'error', text: ev.error || 'stream error' })) }
        }
      }
    } catch (e: any) { patch(() => ({ id: aid, role: 'error', text: String(e) })) }
    finally { setBusy(false) }
  }

  const CHIPS = ['What do you see?', 'Wave at me', 'Battery?', 'Stand up']

  return (
    <section className="agent-dock" aria-label="Agent">
      {/* messages float above the composer, fading up the page */}
      <div className="msg-stream" ref={streamRef} onScroll={onScroll} role="log" aria-live="polite" aria-label="Conversation">
        {msgs.map((m, i, arr) => (
          <div className={`fmsg ${m.role}`} key={m.id} style={{ opacity: stuck ? Math.max(0.35, 1 - (arr.length - 1 - i) * 0.12) : 1 }}>
            {m.reasoning && <ReasoningBlock text={m.reasoning} live={busy && m.id === msgs[msgs.length - 1]?.id && !m.text} />}
            {m.tool && <div className="fmsg-tool"><span className="fmsg-tool-dot" aria-hidden="true" />{m.tool}</div>}
            {m.text ? <div className="fmsg-text">{m.text}</div>
              : (m.role === 'assistant' && busy ? <div className="fmsg-text typing" aria-label="thinking">_</div> : null)}
          </div>
        ))}
        <div ref={endRef} />
      </div>

      {/* fixed composer */}
      <div className="composer">
        {msgs.length === 0 && (
          <div className="composer-chips">
            {CHIPS.map(c => <button key={c} className="cchip" onClick={() => setInput(c)}>{c}</button>)}
          </div>
        )}
        <div className="composer-bar">
          <span className={`composer-status ${ready ? 'ok' : ready === false ? 'err' : ''}`} role="img" aria-label={ready ? `agent online, ${tools} tools` : ready === false ? 'agent offline' : 'agent status unknown'} title={ready ? `${tools} tools` : 'offline'} />
          <input className="composer-input" value={input} placeholder="talk to neon" aria-label="Message to neon"
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') send() }}
            disabled={ready === false} />
          <button className="composer-send" onClick={send} disabled={busy || ready === false} aria-label="Send" title="Send (Enter)">{Ico.send()}</button>
        </div>
      </div>
    </section>
  )
}

function ReasoningBlock({ text, live }: { text: string; live: boolean }) {
  const [open, setOpen] = useState(false)
  const show = open || live
  return (
    <div className="freason">
      <button className="freason-toggle" onClick={() => setOpen(v => !v)} aria-expanded={show}>
        <span className={`freason-spark ${live ? 'live' : ''}`} />{live ? 'thinking' : open ? 'hide reasoning' : 'reasoning'}
      </button>
      {show && <div className="freason-body">{text}</div>}
    </div>
  )
}
