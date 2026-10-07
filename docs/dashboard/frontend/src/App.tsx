import { useEffect, useState } from 'react'
import { useSocket } from './lib/useSocket'
import AuthGate from './components/AuthGate'
import ConfigPanel from './components/ConfigPanel'
import CameraCard from './components/CameraCard'
import LidarView from './components/LidarView'
import TeleopPanel from './components/TeleopPanel'
import AgentDock from './components/AgentDock'
import LogFeed from './components/LogFeed'
import ActivityStrip from './components/ActivityStrip'
import VoiceSheet, { VoicePill, useVoiceStatus } from './components/VoiceSheet'
import TelemetryGraphs from './components/TelemetryGraphs'
import StateCard from './components/StateCard'
import PostureCard from './components/PostureCard'
import { Brand } from './components/Brand'
import { Ico } from './components/Icons'

export default function App() {
  return <AuthGate><Dashboard /></AuthGate>
}

/** Right-side drawer: role=dialog, closes on the scrim click, the close button and Escape. */
export function Drawer({ title, icon, onClose, children, className = '' }: { title: string; icon?: React.ReactNode; onClose: () => void; children: React.ReactNode; className?: string }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="drawer-scrim" onClick={onClose}>
      <div className={`drawer ${className}`} role="dialog" aria-modal="true" aria-label={title} onClick={e => e.stopPropagation()}>
        <div className="drawer-head">
          <span className="drawer-title">{icon && <span className="ic">{icon}</span>}{title}</span>
          <button className="icon-btn sm" onClick={onClose} aria-label={`Close ${title.toLowerCase()}`} title="Close (Esc)">{Ico.close()}</button>
        </div>
        {children}
      </div>
    </div>
  )
}

function Dashboard() {
  const { telemetry, log, stats, conn } = useSocket()
  const [teleop, setTeleop] = useState(false)
  const [showLog, setShowLog] = useState(false)
  const [showConfig, setShowConfig] = useState(false)
  const [showVoice, setShowVoice] = useState(false)
  const voice = useVoiceStatus()
  const [view, setView] = useState<'color' | 'depth' | 'lidar'>('color')
  const t = telemetry
  const connLabel = conn === 'open' ? 'LIVE' : conn === 'connecting' ? 'CONNECTING' : 'OFFLINE'

  return (
    <div className="stage" data-testid="cockpit">
      {/* topbar strip: brand, connection, teleop / log / config */}
      <header className="hud-top">
        <h1 className="sr-only">neon / G1 cockpit</h1>
        <Brand />
        <div className="hud-top-right">
          <div className={`conn-pill ${conn}`} role="status" aria-live="polite"><span className={`dot ${conn}`} />{connLabel}</div>
          <VoicePill st={voice.st} onClick={() => setShowVoice(true)} />
          <button className={teleop ? 'icon-tab on' : 'icon-tab'} onClick={() => setTeleop(v => !v)} aria-pressed={teleop} aria-label="Teleop" title="Teleop" data-testid="tab-teleop">{Ico.headset()}</button>
          <button className={showLog ? 'icon-tab on' : 'icon-tab'} onClick={() => setShowLog(v => !v)} aria-pressed={showLog} aria-label="Activity log" title="Activity log" data-testid="tab-log">{Ico.activity()}</button>
          <button className="icon-tab" onClick={() => setShowConfig(true)} aria-label="Configuration" title="Configuration" data-testid="tab-config">{Ico.sliders()}</button>
        </div>
      </header>

      {teleop ? (
        <main className="teleop-full" aria-label="Teleop"><TeleopPanel /></main>
      ) : (
        <>
          {/* center: camera / depth / lidar stage */}
          <main className="viewstage">
            <div className="view-switch" role="tablist" aria-label="View">
              {(['color', 'depth', 'lidar'] as const).map(v => (
                <button key={v} role="tab" aria-selected={view === v} className={view === v ? 'vpill on' : 'vpill'} onClick={() => setView(v)}>{v}</button>
              ))}
            </div>
            <div className="view-canvas">
              {view === 'lidar'
                ? <LidarView embedded />
                : <CameraCard embedded prefer={view} />}
            </div>
          </main>

          {/* corner cards (on a phone they stack) */}
          <aside className="corner tl" aria-label="Controller">
            <StateCard s={t?.state} ls={t?.lowstate} embedded />
          </aside>
          <div className="rail right">
            <aside className="corner" aria-label="Posture">
              <PostureCard ls={t?.lowstate} embedded />
            </aside>
            {/* what neon is doing right now, across voice / telegram / thinker / chat */}
            <aside className="corner act" aria-label="Activity">
              <ActivityStrip log={log} onMore={() => setShowLog(true)} />
            </aside>
          </div>
          <aside className="corner bl" aria-label="Telemetry">
            <TelemetryGraphs t={t} />
          </aside>

          {/* message stream over the page + fixed bottom composer */}
          <AgentDock />
        </>
      )}

      {showLog && (
        <Drawer title="Activity log" icon={Ico.activity()} onClose={() => setShowLog(false)} className="log">
          <LogFeed log={log} stats={stats} embedded />
        </Drawer>
      )}
      {showConfig && <ConfigPanel onClose={() => setShowConfig(false)} />}
      {showVoice && <VoiceSheet st={voice.st} refresh={voice.refresh} onClose={() => setShowVoice(false)} />}
    </div>
  )
}
