import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Job } from '@/lib/api'

/**
 * Tracks one render job over WebSocket, with polling as a fallback.
 *
 * The pipeline runs in a worker thread and broadcasts on the WS channel; if
 * the socket fails (proxy, restart) we degrade to polling /api/jobs/{id}
 * rather than losing the job silently.
 */
export function useJob(onLog?: (line: string) => void) {
  const [job, setJob] = useState<Job | null>(null)
  const [running, setRunning] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const timerRef = useRef<number | null>(null)
  const logRef = useRef(onLog)
  logRef.current = onLog

  const stop = useCallback(() => {
    wsRef.current?.close()
    wsRef.current = null
    if (timerRef.current) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  useEffect(() => stop, [stop])

  const watch = useCallback(
    (jobId: string) => {
      stop()
      setRunning(true)
      setJob(null)

      const finish = (j: Job) => {
        setJob(j)
        if (j.done) {
          setRunning(false)
          stop()
        }
      }

      const poll = () => {
        if (timerRef.current) return
        timerRef.current = window.setInterval(async () => {
          try {
            const j = await api.getJob(jobId)
            logRef.current?.(`[${j.stage}] ${j.message}`)
            finish(j)
          } catch {
            /* transient; keep polling */
          }
        }, 1500)
      }

      try {
        const proto = location.protocol === 'https:' ? 'wss' : 'ws'
        const ws = new WebSocket(`${proto}://${location.host}/ws/jobs/${jobId}`)
        wsRef.current = ws
        ws.onmessage = (ev) => {
          const d = JSON.parse(ev.data) as Job
          logRef.current?.(`[${d.stage}] ${d.message}`)
          finish(d)
        }
        ws.onerror = () => poll()
        ws.onclose = () => {
          if (running) poll()
        }
      } catch {
        poll()
      }
    },
    [stop, running],
  )

  return { job, running, watch, stop }
}
