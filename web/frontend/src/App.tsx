import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Toaster, toast } from 'sonner'
import { AlertTriangle } from 'lucide-react'
import { Header } from '@/components/Header'
import { ProviderSettings } from '@/components/ProviderSettings'
import { ScriptPanel, type GeneratePayload } from '@/components/ScriptPanel'
import { StatusCard } from '@/components/StatusCard'
import { PreviewCard } from '@/components/PreviewCard'
import { EditPanel } from '@/components/EditPanel'
import { PublishPanel } from '@/components/PublishPanel'
import { LogPanel } from '@/components/LogPanel'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Badge } from '@/components/ui/primitives'
import { useJob } from '@/hooks/useJob'
import { api, errorText, toastError, type Job, type Settings } from '@/lib/api'

export default function App() {
  const { t } = useTranslation()
  const [settings, setSettings] = useState<Settings | null>(null)
  const [lines, setLines] = useState<string[]>([])
  const [jobId, setJobId] = useState<string | null>(null)
  const settingsRef = useRef<HTMLDivElement>(null)

  const onLog = useCallback((line: string) => {
    setLines((v) => [...v.slice(-400), line])
  }, [])

  const { job, running, watch } = useJob(onLog)

  useEffect(() => {
    api
      .getSettings()
      .then(setSettings)
      .catch((e) => toastError(e))
  }, [])

  const generate = async (p: GeneratePayload) => {
    if (!settings?.video.ready) {
      toast.error(t('errors.notConfigured'))
      settingsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      return
    }
    try {
      const r = await api.generate({
        script: p.script,
        name: `web-${Date.now().toString(36)}`,
        style: p.style,
        aspect: p.aspect,
        draft: p.draft,
        use_llm: p.use_llm,
        video_provider: p.video_provider,
        voice_provider: p.voice_provider,
      })
      setJobId(r.job_id)
      onLog(`> job_id = ${r.job_id}`)
      watch(r.job_id)
    } catch (e) {
      const msg = errorText(e)
      toast.error(msg)
      onLog(`ERROR: ${msg}`)
    }
  }

  const finished: Job | null = job

  return (
    <div className="min-h-screen bg-background">
      <Toaster position="top-center" richColors />
      <Header ready={Boolean(settings?.video.ready)} />

      <main className="mx-auto grid max-w-[1400px] grid-cols-1 items-start gap-5 px-6 py-6 lg:grid-cols-[minmax(380px,440px)_1fr]">
        {/* ------------------------------------------------------------ left */}
        <div className="space-y-5" ref={settingsRef}>
          {settings ? (
            <>
              <ProviderSettings settings={settings} onSaved={setSettings} />
              <ScriptPanel
                settings={settings}
                running={running}
                job={job}
                onGenerate={generate}
              />
              <StatusCard settings={settings} />
            </>
          ) : (
            <div className="rounded-lg border bg-card p-5 text-sm text-muted-foreground shadow-sm">
              Loading settings…
            </div>
          )}
        </div>

        {/* ----------------------------------------------------------- right */}
        <div className="space-y-5">
          <PreviewCard job={finished} />

          {finished?.error ? (
            <div className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{finished.error}</span>
            </div>
          ) : null}

          <section className="rounded-lg border bg-card shadow-sm">
            <Tabs defaultValue="edit" className="p-5">
              <TabsList>
                <TabsTrigger value="edit">{t('edit.tab')}</TabsTrigger>
                <TabsTrigger value="publish">{t('publish.tab')}</TabsTrigger>
                <TabsTrigger value="log">{t('log.tab')}</TabsTrigger>
              </TabsList>
              <TabsContent value="edit">
                <EditPanel jobId={jobId} />
              </TabsContent>
              <TabsContent value="publish">
                <PublishPanel videoUrl={finished?.video_url ?? null} />
              </TabsContent>
              <TabsContent value="log">
                <LogPanel lines={lines} />
              </TabsContent>
            </Tabs>
          </section>

          {jobId ? (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Badge variant="outline">job {jobId}</Badge>
              {finished?.done ? t('stage.done') : t('stage.queued')}
            </div>
          ) : null}
        </div>
      </main>
    </div>
  )
}
