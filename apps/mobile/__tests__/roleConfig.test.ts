import {
  canUserVerifyIncident,
  getRoleCapabilities,
} from '../src/navigation/roleConfig';
import { extractIncidentRouteFromNotificationData } from '../src/navigation/guards';
import type { UserProfile } from '../src/shared/types/domain';

function makeUser(
  roleCode: 'admin' | 'supervisor_utama' | 'supervisor_wilayah' | 'koordinator_teknisi' | 'teknisi',
  permissions: string[],
): UserProfile {
  return {
    id: 1,
    username: roleCode,
    email: null,
    full_name: roleCode,
    is_active: true,
    is_on_duty: true,
    role: { id: 1, code: roleCode, name: roleCode },
    permissions,
    scope_type: roleCode === 'admin' || roleCode === 'supervisor_utama' ? 'global' : 'regional',
    regions: [],
  };
}

describe('Role-aware navigation & ADR-011 RBAC guard', () => {
  test('Admin has global admin capabilities but NEVER canVerifyIncident (ADR-011, TC-03)', () => {
    const caps = getRoleCapabilities('admin');
    expect(caps.canManageAdmin).toBe(true);
    expect(caps.isGlobalScope).toBe(true);
    expect(caps.canVerifyIncident).toBe(false);
    expect(caps.visibleTabs).toContain('admin');
    expect(caps.visibleTabs).not.toContain('queue');

    // Even if server permissions array were mistakenly mutated, UI guard still blocks Admin
    const adminUser = makeUser('admin', ['incidents.verify', 'users.manage']);
    expect(canUserVerifyIncident(adminUser)).toBe(false);
  });

  test('Supervisor Utama and Supervisor Wilayah can verify incidents & manage assignments', () => {
    const supUtama = makeUser('supervisor_utama', ['incidents.verify', 'assignments.manage']);
    const supWil = makeUser('supervisor_wilayah', ['incidents.verify', 'assignments.manage']);

    expect(canUserVerifyIncident(supUtama)).toBe(true);
    expect(canUserVerifyIncident(supWil)).toBe(true);
    expect(getRoleCapabilities('supervisor_utama').isGlobalScope).toBe(true);
    expect(getRoleCapabilities('supervisor_wilayah').isGlobalScope).toBe(false);
  });

  test('Koordinator Teknisi can manage assignments but cannot verify incidents', () => {
    const koor = makeUser('koordinator_teknisi', ['assignments.manage']);
    expect(getRoleCapabilities('koordinator_teknisi').canManageAssignment).toBe(true);
    expect(canUserVerifyIncident(koor)).toBe(false);
  });

  test('Teknisi can respond, submit resolution, and use offline drafts', () => {
    const caps = getRoleCapabilities('teknisi');
    expect(caps.canRespondIncident).toBe(true);
    expect(caps.canSubmitResolution).toBe(true);
    expect(caps.canUseOfflineDrafts).toBe(true);
    expect(caps.canVerifyIncident).toBe(false);
    expect(caps.queueTabTitle).toBe('Draft Offline');
  });

  test('Notification deep-link extractor handles incident_id and deep_link URI safely', () => {
    expect(extractIncidentRouteFromNotificationData({ incident_id: 42 })).toBe('/incidents/42');
    expect(
      extractIncidentRouteFromNotificationData({ deep_link: 'prtgschool://incidents/108' }),
    ).toBe('/incidents/108');
    expect(extractIncidentRouteFromNotificationData({ incident_id: 'invalid' })).toBeNull();
    expect(extractIncidentRouteFromNotificationData(null)).toBeNull();
  });
});
