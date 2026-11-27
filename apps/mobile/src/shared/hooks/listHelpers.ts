/**
 * Menggabungkan item halaman berikutnya tanpa duplikasi berdasarkan `id`.
 */
export function mergeUniqueById<T extends { id: number }>(prev: T[], next: T[]): T[] {
  if (prev.length === 0) return next;
  const seen = new Set(prev.map((item) => item.id));
  const appended = next.filter((item) => !seen.has(item.id));
  return appended.length === 0 ? prev : [...prev, ...appended];
}
