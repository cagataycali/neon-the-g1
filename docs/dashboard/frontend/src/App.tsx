import { useState } from 'react'
import { useSocket } from './lib/useSocket'
import AuthGate from './components/AuthGate'
import ConfigPanel from './components/ConfigPanel'
import CameraCard from './components/CameraCard'
import LidarView from './components/LidarView'
import TeleopPanel from './components/TeleopPanel'
import AgentDock from './components/AgentDock'
import LogFeed from './components/LogFeed'
import TelemetryGraphs from './components/TelemetryGraphs'
import StateCard from './components/StateCard'
import PostureCard from './components/PostureCard'

export default function App() {
  return <AuthGate><Dashboard /></AuthGate>
}

function Dashboard() {
  const { telemetry, log, stats, conn } = useSocket()
  const [teleop, setTeleop] = useState(false)
  const [showLog, setShowLog] = useState(false)
  const [showConfig, setShowConfig] = useState(false)
  const [view, setView] = useState<'color' | 'depth' | 'lidar'>('color')
  const t = telemetry
  const connLabel = conn === 'open' ? 'LIVE' : conn === 'connecting' ? 'CONNECTING' : 'OFFLINE'

  return (
    <div className="stage">
      {/* ── floating top bar ── */}
      <header className="hud-top">
        <div className="brand">
          <div className="brand-mark">N</div>
          <div className="brand-txt">
            <h1>NEON<span>·</span>G1</h1>
            <div className="sub">vision · language · action</div>
          </div>
        </div>
        <div className="hud-top-right">
          <div className="conn-pill"><span className={`dot ${conn}`} />{connLabel}</div>
          <button className={teleop ? 'icon-tab on' : 'icon-tab'} onClick={() => setTeleop(v => !v)} title="Teleop">🥽</button>
          <button className={showLog ? 'icon-tab on' : 'icon-tab'} onClick={() => setShowLog(v => !v)} title="Activity log">🧬</button>
          <button className="icon-tab" onClick={() => setShowConfig(true)} title="Configuration">⚙️</button>
        </div>
      </header>

      {teleop ? (
        <div className="teleop-full"><TeleopPanel /></div>
      ) : (
        <>
          {/* ── BIG CENTER: camera / depth / lidar stage ── */}
          <main className="viewstage">
            <div className="view-switch">
              {(['color', 'depth', 'lidar'] as const).map(v => (
                <button key={v} className={view === v ? 'vpill on' : 'vpill'} onClick={() => setView(v)}>{v}</button>
              ))}
            </div>
            <div className="view-canvas">
              {view === 'lidar'
                ? <LidarView embedded />
                : <CameraCard embedded prefer={view} />}
            </div>
          </main>

          {/* ── floating corner widgets (mobile-first: they wrap) ── */}
          <aside className="corner tl">
            <StateCard s={t?.state} ls={t?.lowstate} embedded />
          </aside>
          <aside className="corner tr-below">
            <PostureCard ls={t?.lowstate} embedded />
          </aside>
          <aside className="corner bl">
            <TelemetryGraphs t={t} />
          </aside>

          {/* ── messages stream over the page + fixed bottom composer ── */}
          <AgentDock />
        </>
      )}

      {/* ── activity log drawer (top-right) ── */}
      {showLog && (
        <div className="drawer-scrim" onClick={() => setShowLog(false)}>
          <div className="drawer log" onClick={e => e.stopPropagation()}>
            <div className="drawer-head">
              <span className="drawer-title">Activity Log</span>
              <button className="icon-btn" onClick={() => setShowLog(false)}>✕</button>
            </div>
            <LogFeed log={log} stats={stats} embedded />
          </div>
        </div>
      )}

      {/* ── config drawer ── */}
      {showConfig && <ConfigPanel onClose={() => setShowConfig(false)} />}
    </div>
  )
}
