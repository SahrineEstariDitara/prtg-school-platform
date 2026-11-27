import { mergeUniqueById } from '../src/shared/hooks/listHelpers';
import { extractIncidentRouteFromNotificationData } from '../src/navigation/guards';

describe('Pagination Deduplication & Notification Deep-Link Guards', () => {
  test('mergeUniqueById prevents duplicate rows across pagination overlaps', () => {
    const page1 = [
      { id: 1, name: 'SMAN 1' },
      { id: 2, name: 'SMAN 2' },
    ];
    const page2WithOverlap = [
      { id: 2, name: 'SMAN 2' },
      { id: 3, name: 'SMAN 3' },
    ];

    const merged = mergeUniqueById(page1, page2WithOverlap);
    expect(merged).toEqual([
      { id: 1, name: 'SMAN 1' },
      { id: 2, name: 'SMAN 2' },
      { id: 3, name: 'SMAN 3' },
    ]);
  });

  test('extractIncidentRouteFromNotificationData parses numeric IDs and deep links safely', () => {
    expect(extractIncidentRouteFromNotificationData({ incident_id: 105 })).toBe('/incidents/105');
    expect(extractIncidentRouteFromNotificationData({ incidentId: '88' })).toBe('/incidents/88');
    expect(
      extractIncidentRouteFromNotificationData({ deep_link: 'prtgschool://incidents/42' }),
    ).toBe('/incidents/42');
    expect(extractIncidentRouteFromNotificationData({ incident_id: -1 })).toBeNull();
    expect(extractIncidentRouteFromNotificationData(null)).toBeNull();
  });
});
