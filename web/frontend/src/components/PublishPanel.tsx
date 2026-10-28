import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Upload } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { api, errorText, toastError } from '@/lib/api'

const PRIVACY = ['SELF_ONLY', 'PUBLIC_TO_EVERYONE', 'FOLLOWER_OF_CREATOR'] as const

export function PublishPanel({
  videoUrl,
  videoPath,
}: {
  videoUrl: string | null
  /** Server-side path; falls back to deriving it from videoUrl. */
  videoPath?: string | null
}) {
  const { t } = useTranslation()
  const [title, setTitle] = useState('')
  const [privacy, setPrivacy] = useState<string>('SELF_ONLY')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const target = videoPath || (videoUrl ? videoUrl.replace('/api/video/', 'demo/out/web/') : null)

  const auth = async () => {
    try {
      const r = await api.tiktokAuthUrl()
      window.open(r.auth_url, '_blank', 'width=800,height=700')
      setMsg(t('publish.authorized'))
    } catch (e) {
      toastError(e)
    }
  }

  const upload = async () => {
    if (!target) {
      setMsg(t('publish.needVideo'))
      return
    }
    setBusy(true)
    setMsg('')
    try {
      const r = await api.uploadTiktok({
        video_path: target,
        title,
        privacy_level: privacy,
      })
      setMsg(r.ok ? JSON.stringify(r.result) : r.detail ?? 'upload failed')
    } catch (e) {
      setMsg(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <Label className="text-xs">{t('publish.caption')}</Label>
        <Textarea
          className="min-h-[72px]"
          placeholder={t('publish.captionPlaceholder')}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>

      <div className="space-y-1.5">
        <Label className="text-xs">{t('publish.privacy')}</Label>
        <Select value={privacy} onValueChange={setPrivacy}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PRIVACY.map((p) => (
              <SelectItem key={p} value={p}>
                {t(
                  p === 'SELF_ONLY'
                    ? 'publish.privacySelf'
                    : p === 'PUBLIC_TO_EVERYONE'
                      ? 'publish.privacyPublic'
                      : 'publish.privacyFollowers',
                )}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex flex-col gap-2">
        <Button variant="secondary" onClick={auth}>
          {t('publish.auth')}
        </Button>
        <Button onClick={upload} disabled={busy}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
          {t('publish.upload')}
        </Button>
      </div>

      {msg ? <p className="text-xs text-muted-foreground">{msg}</p> : null}
      <p className="text-xs text-muted-foreground">{t('publish.hint')}</p>
    </div>
  )
}
