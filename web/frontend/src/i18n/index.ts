import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import zh from './locales/zh'
import en from './locales/en'

export const LANGUAGES = [
  { code: 'zh', label: '简体中文' },
  { code: 'en', label: 'English' },
] as const

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      zh: { translation: zh },
      en: { translation: en },
    },
    // Chinese-first: this repo's user base is CN, and every vendor note is
    // written from a CN-perspective anyway.
    fallbackLng: 'zh',
    supportedLngs: ['zh', 'en'],
    interpolation: { escapeValue: false },
    detection: {
      order: ['localStorage', 'navigator'],
      caches: ['localStorage'],
      lookupLocalStorage: 'avb.lang',
    },
  })

export function applyDocumentLang(lng: string) {
  document.documentElement.lang = lng === 'en' ? 'en' : 'zh-CN'
}

applyDocumentLang(i18n.language)

export default i18n
