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
    // English-first: the product targets overseas DTC ads, so the default
    // experience is English. Chinese is one click away in the header, and an
    // explicit choice is remembered in localStorage.
    fallbackLng: 'en',
    supportedLngs: ['zh', 'en'],
    interpolation: { escapeValue: false },
    detection: {
      // Deliberately no 'navigator': sniffing the browser would silently
      // override the default and two users would see different products.
      order: ['localStorage'],
      caches: ['localStorage'],
      lookupLocalStorage: 'avb.lang',
    },
  })

export function applyDocumentLang(lng: string) {
  document.documentElement.lang = lng === 'en' ? 'en' : 'zh-CN'
}

applyDocumentLang(i18n.language)

export default i18n
