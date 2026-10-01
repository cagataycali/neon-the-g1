import { useState } from 'react'
import { Ico } from './Icons'

// WebXR teleop MUST be local (headset to robot direct, low latency).
// Default to the robot's LAN IP. Ports: 8013 HTTPS page, 8012 WSS pose,
// 60001 WebRTC camera. (Run ./run_xr_teleop.sh + teleimager on the robot.)
const DEFAULT_IP = '192.168.1.175'

export default function TeleopPanel() {
  const guess = typeof location !== 'undefined' && /^\d+\.\d+\.\d+\.\d+$/.test(location.hostname)
    ? location.hostname : DEFAULT_IP
  const [ip, setIp] = useState(guess)

  const page = `https://${ip}:8013/`
  const ws = `wss://${ip}:8012/xr`
  const rtc = `https://${ip}:60001/offer`

  return (
    <div className="grid">
      <div className="card span-7">
        <div className="card-title"><span className="ic">{Ico.headset()}</span> WebXR teleop, Meta Quest 3 (LAN)</div>
        <p className="hint">
          Drive the G1 from your headset: hand, controller and head poses go through IK to DDS, with the
          robot's stereo camera in your eyes. <b>Must be on the same WiFi</b> as the robot
          (WebXR needs HTTPS + WSS + low latency; this is intentionally NOT tunneled).
        </p>
        <label className="lbl" htmlFor="teleop-ip">Robot LAN IP</label>
        <input id="teleop-ip" className="inp mono" value={ip} onChange={(e) => setIp(e.target.value)} />
        <div className="url-list">
          <UrlRow label="Open on Quest 3" url={page} primary />
          <UrlRow label="Pose uplink (WSS)" url={ws} />
          <UrlRow label="Camera downlink (WebRTC)" url={rtc} />
        </div>
        <div className="launch-box">
          <div className="lbl">Start on the robot (two terminals)</div>
          <div className="term">
            <div className="term-head"><span className="ic">{Ico.terminal()}</span>robot shell</div>
            <pre className="code">{`# 1) WebXR pose bridge (HTTPS :8013 + WSS :8012)
./run_xr_teleop.sh

# 2) Camera downlink for the headset (WebRTC :60001)
./run_teleimager.sh`}</pre>
          </div>
        </div>
      </div>
      <div className="card span-5">
        <div className="card-title"><span className="ic">{Ico.monitor()}</span> Teleop page preview</div>
        <div className="iframe-wrap">
          <iframe className="teleop-iframe" src={page} title="WebXR Teleop"
                  sandbox="allow-scripts allow-same-origin allow-forms" />
          <div className="iframe-note">
            Blank? The bridge is not running, or accept the self-signed cert first:
            open <a href={page} target="_blank" rel="noreferrer">{page}</a> directly.
          </div>
        </div>
      </div>
    </div>
  )
}

function UrlRow({ label, url, primary }: { label: string; url: string; primary?: boolean }) {
  const [copied, setCopied] = useState(false)
  const copy = () => navigator.clipboard?.writeText(url).then(() => {
    setCopied(true); setTimeout(() => setCopied(false), 1200)
  })
  return (
    <div className={`url-row ${primary ? 'primary' : ''}`}>
      <div><div className="url-label">{label}</div><div className="url-val mono">{url}</div></div>
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="mini-btn" onClick={copy} aria-label={`Copy ${label} URL`}><span className="ic">{copied ? Ico.check() : Ico.copy()}</span>{copied ? 'copied' : 'copy'}</button>
        <a className="mini-btn" href={url} target="_blank" rel="noreferrer" aria-label={`Open ${label} in a new tab`}><span className="ic">{Ico.external()}</span>open</a>
      </div>
    </div>
  )
}
