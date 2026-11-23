/**
 * Deep-link parser & auth guard untuk notifikasi push (SAD §9.4, TC-13).
 * Deep link dari notifikasi (background/terminated) hanya boleh membuka incident
 * setelah autentikasi dan otorisasi selesai.
 */

export function extractIncidentRouteFromNotificationData(
  data: Record<string, unknown> | null | undefined,
): string | null {
  if (!data) return null;

  const rawId = data.incident_id ?? data.incidentId;
  if (rawId !== undefined && rawId !== null && String(rawId).trim() !== '') {
    const parsed = Number(rawId);
    if (Number.isInteger(parsed) && parsed > 0) {
      return `/incidents/${parsed}`;
    }
  }

  const rawDeepLink = typeof data.deep_link === 'string' ? data.deep_link : null;
  if (rawDeepLink) {
    const match = rawDeepLink.match(/incidents\/(\d+)/i);
    if (match?.[1]) {
      return `/incidents/${match[1]}`;
    }
  }

  return null;
}
