// Shapes mirror server.py telemetry payloads. All fields optional/defensive
// since DDS may be offline the server returns status:'error' per field.

export interface G1State {
  status?: string
  mode?: { name?: string } | null
  fsm_id?: number | null
  fsm_name?: string | null
  fsm_mode?: number | null
  balance_mode?: number | null
  stand_height?: number | null
  swing_height?: number | null
  arm_ready?: boolean
  mode_machine?: number | null
  message?: string
}

export interface Battery {
  status?: string
  soc_pct?: number
  soh_pct?: number
  voltage_v?: number
  current_a?: number
  cycle?: number
  temp_max_c?: number | null
  stale_s?: number
  message?: string
}

export interface LowState {
  status?: string
  imu_rpy?: number[]
  legs?: {
    L_hip_pitch?: number
    L_knee?: number
    R_hip_pitch?: number
    R_knee?: number
    avg_knee?: number
  }
  posture?: string
  max_leg_tau?: number
  mode_machine?: number
  mode_pr?: number
  tick?: number
  message?: string
}

export interface Mainboard {
  status?: string
  fan_speed?: number[]
  temperature?: number[]
  cpu_temperature?: number
  message?: string
}

export interface Telemetry {
  ts: number
  iface?: string
  state?: G1State
  battery?: Battery
  lowstate?: LowState
  mainboard?: Mainboard
  lidar?: Record<string, unknown>
}

export interface LogEntry {
  persona: string
  role: string
  text: string
  meta?: Record<string, unknown> | null
  ts: string
}

export interface LogStats {
  total?: number
  by_persona?: Record<string, number>
}
