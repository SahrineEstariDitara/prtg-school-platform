import type { NetworkCounts, PrtgSensorItem } from '../../shared/types/domain';

export function computeOnlinePercentage(counts: NetworkCounts, total: number): number {
  if (total <= 0) return 0;
  return Math.round((counts.online / total) * 100);
}

export function sortSensorsBySeverity(sensors: PrtgSensorItem[]): PrtgSensorItem[] {
  const rank: Record<string, number> = {
    offline: 0,
    warning: 1,
    unknown: 2,
    online: 3,
  };
  return [...sensors].sort((a, b) => {
    if (a.is_required !== b.is_required) {
      return a.is_required ? -1 : 1;
    }
    const ra = rank[a.normalized_status] ?? 9;
    const rb = rank[b.normalized_status] ?? 9;
    if (ra !== rb) return ra - rb;
    return a.sensor_name.localeCompare(b.sensor_name);
  });
}
