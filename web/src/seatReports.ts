import type { SeatMeta } from './types'

/** The same date can be rebuilt; version all entry points by verified content. */
export function seatHtmlUrl(report: SeatMeta): string {
  const version = report.html_hash || report.generated_at
  const base = `/api/seat/${report.date}/html`
  return version ? `${base}?v=${encodeURIComponent(version)}` : base
}

export function latestSeatDate(items: SeatMeta[]): string | null {
  return items.find((item) => item.has_html)?.date
    ?? items.find((item) => item.has_image)?.date ?? null
}

/** Follow the latest report, but do not interrupt someone reading an archive. */
export function refreshedSeatDate(
  current: string | null, items: SeatMeta[], previousLatest: string | null,
): string | null {
  if (!previousLatest || current === previousLatest
      || !items.some((item) => item.date === current && (item.has_html || item.has_image))) {
    return latestSeatDate(items)
  }
  return current
}
