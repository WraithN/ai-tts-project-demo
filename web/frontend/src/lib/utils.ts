import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatUsd(v: number | null | undefined) {
  if (v == null) return '—'
  return `$${v.toFixed(v < 1 ? 3 : 2)}`
}

export function formatDuration(v: number | null | undefined) {
  if (v == null) return '—'
  return `${v.toFixed(1)}s`
}
