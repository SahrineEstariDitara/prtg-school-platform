/**
 * Hoisted Intl formatters (vercel-react-native-skills: js-hoist-intl).
 */

const dateTimeFormatter = new Intl.DateTimeFormat('id-ID', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});

const shortTimeFormatter = new Intl.DateTimeFormat('id-ID', {
  hour: '2-digit',
  minute: '2-digit',
});

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return 'Belum tercatat';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return dateTimeFormatter.format(d);
}

export function formatShortTime(iso: string | null | undefined): string {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '-';
  return shortTimeFormatter.format(d);
}

export function formatDurationHours(hours: number | null | undefined): string {
  if (typeof hours !== 'number' || Number.isNaN(hours)) return '-';
  if (hours < 1) {
    return `${Math.round(hours * 60)} menit`;
  }
  if (hours < 24) {
    return `${hours.toFixed(1)} jam`;
  }
  const days = Math.floor(hours / 24);
  const rem = Math.round(hours % 24);
  return rem > 0 ? `${days} hari ${rem} jam` : `${days} hari`;
}
