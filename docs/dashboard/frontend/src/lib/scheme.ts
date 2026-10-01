// Colour scheme: html[data-scheme=paper|dark]. index.html sets it before paint from localStorage "neon-scheme"
// (paper | dark) or the OS; this hook is the Configuration toggle. "auto" clears the stored choice and follows the OS again.
import { useCallback, useEffect, useState } from 'react'

export type Scheme = 'auto' | 'paper' | 'dark'
const KEY = 'neon-scheme'
const read = (): Scheme => { try { const v = localStorage.getItem(KEY); return v === 'paper' || v === 'dark' ? v : 'auto' } catch { return 'auto' } }
const osDark = () => matchMedia('(prefers-color-scheme: dark)').matches
export const apply = (s: Scheme) => document.documentElement.setAttribute('data-scheme', (s === 'auto' ? osDark() : s === 'dark') ? 'dark' : 'paper')

export function useScheme(): [Scheme, (s: Scheme) => void] {
  const [scheme, set] = useState<Scheme>(read)
  const setScheme = useCallback((s: Scheme) => {
    try { s === 'auto' ? localStorage.removeItem(KEY) : localStorage.setItem(KEY, s) } catch {}
    set(s); apply(s)
  }, [])
  useEffect(() => {
    const mq = matchMedia('(prefers-color-scheme: dark)')
    const h = () => { if (read() === 'auto') apply('auto') }
    mq.addEventListener('change', h); return () => mq.removeEventListener('change', h)
  }, [])
  return [scheme, setScheme]
}
