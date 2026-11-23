import type { RoleCode, UserProfile } from '../shared/types/domain';

export type MainTabName =
  | 'dashboard'
  | 'schools'
  | 'incidents'
  | 'queue'
  | 'admin'
  | 'profile';

export interface RoleCapabilities {
  roleCode: RoleCode;
  displayName: string;
  visibleTabs: MainTabName[];
  queueTabTitle: string;
  canViewSchools: boolean;
  canManageAssignment: boolean;
  canVerifyIncident: boolean;
  canRespondIncident: boolean;
  canSubmitResolution: boolean;
  canUseOfflineDrafts: boolean;
  canManageAdmin: boolean;
  isGlobalScope: boolean;
}

const ROLE_CAPABILITIES: Record<RoleCode, RoleCapabilities> = {
  admin: {
    roleCode: 'admin',
    displayName: 'Admin Sistem',
    visibleTabs: ['dashboard', 'schools', 'incidents', 'admin', 'profile'],
    queueTabTitle: 'Antrean',
    canViewSchools: true,
    canManageAssignment: false,
    /** ADR-011: Admin TIDAK PERNAH boleh memverifikasi incident. */
    canVerifyIncident: false,
    canRespondIncident: false,
    canSubmitResolution: false,
    canUseOfflineDrafts: false,
    canManageAdmin: true,
    isGlobalScope: true,
  },
  supervisor_utama: {
    roleCode: 'supervisor_utama',
    displayName: 'Supervisor Utama',
    visibleTabs: ['dashboard', 'schools', 'incidents', 'queue', 'profile'],
    queueTabTitle: 'Verifikasi',
    canViewSchools: true,
    canManageAssignment: true,
    canVerifyIncident: true,
    canRespondIncident: false,
    canSubmitResolution: false,
    canUseOfflineDrafts: false,
    canManageAdmin: false,
    isGlobalScope: true,
  },
  supervisor_wilayah: {
    roleCode: 'supervisor_wilayah',
    displayName: 'Supervisor Wilayah',
    visibleTabs: ['dashboard', 'schools', 'incidents', 'queue', 'profile'],
    queueTabTitle: 'Verifikasi',
    canViewSchools: true,
    canManageAssignment: true,
    canVerifyIncident: true,
    canRespondIncident: false,
    canSubmitResolution: false,
    canUseOfflineDrafts: false,
    canManageAdmin: false,
    isGlobalScope: false,
  },
  koordinator_teknisi: {
    roleCode: 'koordinator_teknisi',
    displayName: 'Koordinator Teknisi',
    visibleTabs: ['dashboard', 'schools', 'incidents', 'queue', 'profile'],
    queueTabTitle: 'Tim & Tugas',
    canViewSchools: true,
    canManageAssignment: true,
    canVerifyIncident: false,
    canRespondIncident: false,
    canSubmitResolution: false,
    canUseOfflineDrafts: false,
    canManageAdmin: false,
    isGlobalScope: false,
  },
  teknisi: {
    roleCode: 'teknisi',
    displayName: 'Teknisi Lapangan',
    visibleTabs: ['dashboard', 'incidents', 'queue', 'profile'],
    queueTabTitle: 'Draft Offline',
    canViewSchools: true,
    canManageAssignment: false,
    canVerifyIncident: false,
    canRespondIncident: true,
    canSubmitResolution: true,
    canUseOfflineDrafts: true,
    canManageAdmin: false,
    isGlobalScope: false,
  },
};

export function getRoleCapabilities(roleCode?: RoleCode | null): RoleCapabilities {
  if (!roleCode || !(roleCode in ROLE_CAPABILITIES)) {
    return ROLE_CAPABILITIES.teknisi;
  }
  return ROLE_CAPABILITIES[roleCode];
}

export function getUserCapabilities(user: UserProfile | null): RoleCapabilities {
  return getRoleCapabilities(user?.role?.code);
}

export function hasServerPermission(user: UserProfile | null, permission: string): boolean {
  if (!user) return false;
  return Array.isArray(user.permissions) && user.permissions.includes(permission);
}

/**
 * Cek eksplisit apakah user berhak memverifikasi incident (Approve/Reject/Recheck).
 * Wajib cocok dengan server permission `incidents.verify` DAN bukan role `admin`.
 */
export function canUserVerifyIncident(user: UserProfile | null): boolean {
  if (!user || !user.role) return false;
  if (user.role.code === 'admin') return false;
  const caps = getRoleCapabilities(user.role.code);
  return caps.canVerifyIncident && hasServerPermission(user, 'incidents.verify');
}
