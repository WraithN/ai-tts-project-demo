import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, Languages, Moon, Sun, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Badge } from '@/components/ui/primitives'
import { LANGUAGES, applyDocumentLang } from '@/i18n'
import { cn } from '@/lib/utils'

function useTheme() {
  const [dark, setDark] = useState(() =>
    window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false,
  )
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
  }, [dark])
  return { dark, toggle: () => setDark((v) => !v) }
}

export function Header({ ready }: { ready: boolean }) {
  const { t, i18n } = useTranslation()
  const { dark, toggle } = useTheme()

  const setLang = (code: string) => {
    void i18n.changeLanguage(code)
    applyDocumentLang(code)
  }

  return (
    <header className="sticky top-0 z-40 border-b bg-background/85 backdrop-blur">
      <div className="mx-auto flex max-w-[1400px] items-center justify-between gap-4 px-6 py-3">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <Sparkles className="h-5 w-5" />
          </div>
          <div>
            <div className="text-[15px] font-semibold leading-tight">{t('app.title')}</div>
            <div className="text-xs text-muted-foreground">{t('app.subtitle')}</div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Badge variant={ready ? 'success' : 'secondary'} className="hidden sm:inline-flex">
            <span
              className={cn(
                'mr-1.5 h-1.5 w-1.5 rounded-full',
                ready ? 'bg-emerald-500' : 'bg-muted-foreground',
              )}
            />
            {ready ? t('status.ready') : t('status.missing')}
          </Badge>

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" title={t('app.lang')}>
                <Languages className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {LANGUAGES.map((l) => (
                <DropdownMenuItem
                  key={l.code}
                  onSelect={() => setLang(l.code)}
                  className="justify-between"
                >
                  {l.label}
                  {i18n.language?.startsWith(l.code) ? <Check className="h-4 w-4" /> : null}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>

          <Button
            variant="ghost"
            size="icon"
            onClick={toggle}
            title={`${t('app.theme')}: ${dark ? t('app.themeDark') : t('app.themeLight')}`}
          >
            {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>
        </div>
      </div>
    </header>
  )
}
