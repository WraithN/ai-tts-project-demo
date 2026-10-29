import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Toaster, toast } from 'sonner'
import { AlertTriangle, Send, Settings as SettingsIcon, Sparkles, Terminal, Wand2 } from 'lucide-react'
import { Header } from '@/components/Header'
import { ProviderSettings } from '@/components/ProviderSettings'
import { ScriptPanel, type GeneratePayload } from '@/components/ScriptPanel'
import { BriefPanel } from '@/components/BriefPanel'
import { SceneConfirm } from '@/components/SceneConfirm'
import { Stepper, type StepId } from '@/components/Stepper'
import { StatusCard } from '@/components/StatusCard'
import { PreviewCard } from '@/components/PreviewCard'
import { EditPanel } from '@/components/EditPanel'
import { PublishPanel } from '@/components/PublishPanel'
import { LogPanel } from '@/components/LogPanel'
import { TaskPanel, type ActiveTask, type TaskKind } from '@/components/TaskPanel'
import { Badge } from '@/components/ui/primitives'
import { Panel, PanelHeader, PanelBody } from '@/components/ui/panel'
import { useJob } from '@/hooks/useJob'
import {
  api, errorText, toastError, waitJob,
  type Job, type Plan, type PlanScene, type ScriptDraft, type Settings,
} from '@/lib/api'
import { cn } from '@/lib/utils'

type Page = 'settings' | 'create'

export default function App() {
  const { t } = useTranslation()
  const [page, setPage] = useState<Page>('create')
  const [settings, setSettings] = useState<Settings | null>(null)
  const [lines, setLines] = useState<string[]>([])

  // --- creation flow ----------------------------------------------------
  const [step, setStep] = useState<StepId>('script')
  const [plan, setPlan] = useState<Plan | null>(null)
  const [hasScripts, setHasScripts] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  /** The generation task currently running (scripts / plan / render). */
  const [task, setTask] = useState<ActiveTask | null>(null)
  /** Last payload used for step 1, so "re-parse" can redo it verbatim. */
  const [scriptPayload, setScriptPayload] = useState<GeneratePayload | null>(null)

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

  const ready = Boolean(settings?.video.ready)

  /**
   * Run any generation job (scripts / plan / render) as a tracked task:
   * start it, watch it for live progress, and await its final result.
   * The TaskPanel reads `task` + `job` to render the progress UI.
   */
  const runJob = useCallback(
    async <T,>(kind: TaskKind, start: () => Promise<{ job_id: string }>): Promise<T> => {
      setTask({ kind, startedAt: Date.now() })
      try {
        const { job_id } = await start()
        watch(job_id)
        return await waitJob<T>(job_id)
      } finally {
        setTask(null)
      }
    },
    [watch],
  )

  // ---- step 0: brief -> 4 ad scripts ------------------------------------
  const runScripts = useCallback(
    (payload: Parameters<typeof api.scripts>[0]) =>
      runJob<{ brief: { product: string; style: string }; scripts: ScriptDraft[] }>(
        'scripts',
        () => api.scripts(payload),
      ),
    [runJob],
  )

  // ---- step 1: script -> scene copy ------------------------------------
  const makePlan = async (p: GeneratePayload) => {
    if (!ready) {
      toast.error(t('errors.notConfigured'))
      setPage('settings')
      return
    }
    setScriptPayload(p)
    try {
      const r = await runJob<Plan>('plan', () =>
        api.plan({
          script: p.script,
          name: `web-${Date.now().toString(36)}`,
          style: p.style ?? 'dr_vsl',
          use_llm: p.use_llm,
        }),
      )
      setPlan(r)
      onLog(`> plan ${r.plan_id} · ${r.scenes.length} scenes`)
      setStep('confirm')
      toast.success(t('confirm.planned', { n: r.scenes.length }))
    } catch (e) {
      const msg = errorText(e)
      toast.error(msg)
      onLog(`ERROR: ${msg}`)
    }
  }

  const reparse = () => {
    if (scriptPayload) void makePlan(scriptPayload)
  }

  // ---- step 2 -> 3: confirm -> render (this is the step that spends) ----
  // Visual style + video model are picked in SceneConfirm at confirm time,
  // not in the brief — they only matter once pixels start costing money.
  const confirmScenes = async (
    scenes: PlanScene[],
    renderStyle: string,
    videoProvider: string | null,
  ) => {
    if (!plan) return
    if (!ready) {
      toast.error(t('errors.notConfigured'))
      setPage('settings')
      return
    }
    const p = scriptPayload
    try {
      const r = await api.render({
        plan_id: plan.plan_id,
        scenes: scenes.map((s) => ({
          id: s.id,
          vo: s.vo,
          prompt: s.prompt,
          shot: s.shot,
          angle: s.angle,
          elements: s.elements,
        })),
        name: plan.name,
        style: renderStyle || plan.style,
        aspect: p?.aspect ?? '9:16',
        draft: p?.draft ?? false,
        video_provider: videoProvider ?? null,
        voice_provider: p?.voice_provider ?? null,
        target_seconds: p?.target_seconds ?? null,
        reference_images: p?.reference_images ?? [],
      })
      setJobId(r.job_id)
      onLog(`> confirmed ${scenes.length} scenes · job ${r.job_id}`)
      setStep('render')
      watch(r.job_id)
    } catch (e) {
      const msg = errorText(e)
      toast.error(msg)
      onLog(`ERROR: ${msg}`)
    }
  }

  // ---- step 4: conversational edit -> partial re-render -----------------
  const onEditRender = useCallback(
    (newJobId: string) => {
      setJobId(newJobId)
      onLog(`> re-render job ${newJobId}`)
      setStep('refine')
      watch(newJobId)
    },
    [onLog, watch],
  )

  // Advance to refine once the first render lands.
  useEffect(() => {
    if (job?.done && !job.error && step === 'render') setStep('refine')
  }, [job?.done, job?.error, step])

  const maxReached: StepId = useMemo(() => {
    if (job?.done && !job.error) return 'publish'
    if (jobId) return 'refine'
    if (plan) return 'confirm'
    return 'script'
  }, [job?.done, job?.error, jobId, plan])

  const finished: Job | null = job
  // "Render done" must be a *render* job's completion — the plan job also
  // finishes with done=true, and without the kind check the task panel
  // showed a green check on "Video render" right after planning.
  const hasVideo = Boolean(
    jobId && finished?.done && !finished?.error && (finished.kind === 'render' || finished.video_url),
  )

  return (
    <div className="relative min-h-screen bg-background">
      <Toaster position="top-center" richColors />

      {/* decorative brand backdrop */}
      <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
        <div className="absolute -left-40 -top-40 h-[26rem] w-[26rem] rounded-full bg-primary/10 blur-3xl animate-pulse-soft" />
        <div className="absolute -right-32 top-1/4 h-[30rem] w-[30rem] rounded-full bg-violet-500/10 blur-3xl" />
        <div className="absolute bottom-0 left-1/3 h-[24rem] w-[24rem] rounded-full bg-pink-500/10 blur-3xl" />
      </div>

      <Header ready={ready} />

      {/* --------------------------------------------------- page switcher */}
      <nav className="border-b bg-card/60 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] gap-1 px-6 pt-3">
          {(
            [
              ['create', Sparkles, t('nav.create')],
              ['settings', SettingsIcon, t('nav.settings')],
            ] as const
          ).map(([id, Icon, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setPage(id)}
              className={cn(
                '-mb-px flex items-center gap-1.5 rounded-t-lg border-b-2 px-3 py-2 text-xs font-medium transition-colors',
                page === id
                  ? 'border-primary text-foreground'
                  : 'border-transparent text-muted-foreground hover:text-foreground',
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
              {id === 'settings' && !ready ? (
                <span className="ml-1 rounded-full bg-destructive/15 px-1.5 text-[10px] text-destructive">
                  !
                </span>
              ) : null}
            </button>
          ))}
        </div>
      </nav>

      <main className="mx-auto max-w-[1400px] px-6 py-6">
        {/* ==================================================== settings */}
        {page === 'settings' ? (
          <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-[minmax(420px,560px)_1fr]">
            <div className="space-y-5">
              {settings ? (
                <>
                  <ProviderSettings settings={settings} onSaved={setSettings} />
                  <StatusCard settings={settings} />
                </>
              ) : (
                <div className="rounded-xl border bg-card p-5 text-sm text-muted-foreground shadow-sm">
                  Loading settings…
                </div>
              )}
            </div>
            <div className="space-y-5">
              <Panel>
                <PanelHeader title={t('settings.whatTitle')} icon={SettingsIcon} />
                <PanelBody className="space-y-3">
                  <p className="text-xs leading-relaxed text-muted-foreground">
                    {t('settings.whatBody')}
                  </p>
                  <ul className="space-y-1.5 text-xs text-muted-foreground">
                    <li>· {t('settings.point1')}</li>
                    <li>· {t('settings.point2')}</li>
                    <li>· {t('settings.point3')}</li>
                  </ul>
                </PanelBody>
              </Panel>
              <LogPanel lines={lines} />
            </div>
          </div>
        ) : (
          /* ==================================================== create */
          <div className="space-y-5">
            {step === 'script' && !plan ? <CreateHero /> : null}

            <div className="rounded-xl border bg-card px-4 py-3 shadow-sm">
              <Stepper current={step} maxReached={maxReached} onSelect={setStep} />
            </div>

            <div
              key={step}
              className="grid animate-fade-in grid-cols-1 items-start gap-5 lg:grid-cols-[minmax(0,1fr)_380px]"
            >
              {/* -------------------------------------------------- left */}
              <div className="space-y-5">
                {step === 'script' || !settings ? (
                  settings ? (
                    <BriefPanel
                      settings={settings}
                      planBusy={task?.kind === 'plan'}
                      scriptsBusy={task?.kind === 'scripts'}
                      runScripts={runScripts}
                      onScriptsReady={() => setHasScripts(true)}
                      onPlan={makePlan}
                    />
                  ) : (
                    <div className="rounded-xl border bg-card p-5 text-sm text-muted-foreground shadow-sm">
                      Loading settings…
                    </div>
                  )
                ) : null}

                {step === 'confirm' && plan && settings ? (
                  <SceneConfirm
                    scenes={plan.scenes}
                    settings={settings}
                    busy={running || task !== null}
                    onBack={() => setStep('script')}
                    onReparse={reparse}
                    onConfirm={confirmScenes}
                  />
                ) : null}

                {(step === 'render' || step === 'refine') && settings ? (
                  <ScriptPanel
                    settings={settings}
                    running={running}
                    job={job}
                    onPlan={makePlan}
                  />
                ) : null}

                {step === 'refine' ? (
                  <Panel>
                    <PanelHeader title={t('edit.title')} icon={Wand2} />
                    <PanelBody>
                      <EditPanel jobId={jobId} onRender={onEditRender} />
                    </PanelBody>
                  </Panel>
                ) : null}

                {step === 'publish' ? (
                  <Panel>
                    <PanelHeader title={t('publish.title')} icon={Send} />
                    <PanelBody>
                      <PublishPanel
                        videoUrl={finished?.video_url ?? null}
                        videoPath={finished?.video_path ?? null}
                      />
                    </PanelBody>
                  </Panel>
                ) : null}
              </div>

              {/* ------------------------------------------------- right */}
              {/* Fixed layout: results + task progress always live here,
                  regardless of which step the left column is showing. On
                  large screens it stays pinned so a long scene list on the
                  left never leaves the progress panel stranded below. */}
              <div className="space-y-5 lg:sticky lg:top-[72px] lg:self-start">
                <TaskPanel
                  active={task}
                  job={job}
                  hasScripts={hasScripts}
                  hasPlan={Boolean(plan)}
                  hasVideo={hasVideo}
                />

                {jobId ? <PreviewCard job={finished} /> : null}

                {finished?.error ? (
                  <div className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>{finished.error}</span>
                  </div>
                ) : null}

                <Panel>
                  <PanelHeader title={t('log.tab')} icon={Terminal} />
                  <PanelBody>
                    <LogPanel lines={lines} />
                  </PanelBody>
                </Panel>

                {jobId ? (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Badge variant="outline">job {jobId}</Badge>
                    {finished?.done ? t('stage.done') : t('stage.queued')}
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  )
}

/** Landing hero shown on the create page before a brief is drafted. */
function CreateHero() {
  const { t } = useTranslation()
  return (
    <div className="relative overflow-hidden rounded-xl border bg-brand-soft p-6 shadow-sm ring-1 ring-brand/20">
      <div className="flex items-start gap-4">
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand text-white shadow-md">
          <Sparkles className="h-6 w-6" />
        </span>
        <div className="min-w-0">
          <h1 className="text-lg font-semibold leading-tight">{t('hero.title')}</h1>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted-foreground">
            {t('hero.sub')}
          </p>
        </div>
      </div>
    </div>
  )
}
