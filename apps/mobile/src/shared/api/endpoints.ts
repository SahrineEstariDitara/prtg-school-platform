import { apiRequest } from './client';
import type { PaginatedResponse } from '../types/api';
import type {
  AuditLogItem,
  AuthTokenResponse,
  DashboardSummary,
  IncidentDetail,
  IncidentHistoryItem,
  IncidentSummary,
  MonitoringRegionSummary,
  NotificationItem,
  PrtgSensorItem,
  RecoveryPolicyDetail,
  RegionRef,
  RoleCode,
  RoleRef,
  SchoolDetail,
  SchoolSummary,
  SyncOperationInput,
  SyncOperationResult,
  TechnicianWorkloadItem,
  UserProfile,
} from '../types/domain';

export const authApi = {
  login: (username: string, password: string) =>
    apiRequest<AuthTokenResponse>('/auth/login', {
      method: 'POST',
      body: { username, password },
      skipAuth: true,
    }),

  logout: (refreshToken: string | null) =>
    apiRequest<{ logged_out: boolean; refresh_token_revoked: boolean }>('/auth/logout', {
      method: 'POST',
      body: { refresh_token: refreshToken ?? '' },
    }),

  me: () => apiRequest<UserProfile>('/me'),
};

export const monitoringApi = {
  dashboard: (regionId?: number) =>
    apiRequest<DashboardSummary>('/dashboard', {
      query: { region_id: regionId },
    }),

  summaryByRegion: (regionId?: number) =>
    apiRequest<MonitoringRegionSummary>('/monitoring/summary', {
      query: { region_id: regionId },
    }),

  listSchools: (params?: {
    q?: string;
    region_id?: number;
    network_status?: string;
    page?: number;
    per_page?: number;
  }) =>
    apiRequest<PaginatedResponse<SchoolSummary>>('/schools', {
      query: params,
    }),

  schoolDetail: (schoolId: number) => apiRequest<SchoolDetail>(`/schools/${schoolId}`),

  schoolSensors: (schoolId: number) =>
    apiRequest<{
      school_id: number;
      school_name: string;
      network_status: string;
      is_stale: boolean;
      last_checked_at: string | null;
      sensors: PrtgSensorItem[];
    }>(`/schools/${schoolId}/sensors`),

  schoolIncidents: (schoolId: number, page = 1, perPage = 20) =>
    apiRequest<PaginatedResponse<IncidentSummary>>(`/schools/${schoolId}/incidents`, {
      query: { page, per_page: perPage },
    }),
};

export const incidentsApi = {
  list: (params?: {
    status?: string;
    region_id?: number;
    school_id?: number;
    assigned_to_me?: boolean;
    page?: number;
    per_page?: number;
  }) =>
    apiRequest<PaginatedResponse<IncidentSummary>>('/incidents', {
      query: params,
    }),

  detail: (incidentId: number) => apiRequest<IncidentDetail>(`/incidents/${incidentId}`),

  history: (incidentId: number) =>
    apiRequest<{ incident_id: number; items: IncidentHistoryItem[] }>(
      `/incidents/${incidentId}/history`,
    ),

  listTechnicians: (regionId?: number) =>
    apiRequest<{ items: TechnicianWorkloadItem[]; total: number }>('/technicians', {
      query: { region_id: regionId },
    }),

  assign: (incidentId: number, technicianId: number, reason: string) =>
    apiRequest<Record<string, unknown>>(`/incidents/${incidentId}/assign`, {
      method: 'POST',
      body: { technician_id: technicianId, reason },
    }),

  reassign: (assignmentId: number, technicianId: number, reason: string) =>
    apiRequest<Record<string, unknown>>(`/assignments/${assignmentId}`, {
      method: 'PATCH',
      body: { technician_id: technicianId, reason },
    }),

  acknowledge: (incidentId: number) =>
    apiRequest<Record<string, unknown>>(`/incidents/${incidentId}/acknowledge`, {
      method: 'POST',
    }),

  createResponse: (
    incidentId: number,
    payload: {
      action_summary: string;
      result_notes?: string | null;
      client_operation_id?: string | null;
      attachment?: { uri: string; name: string; type: string } | null;
    },
  ) => {
    if (payload.attachment) {
      const fd = new FormData();
      fd.append('action_summary', payload.action_summary);
      if (payload.result_notes) {
        fd.append('result_notes', payload.result_notes);
      }
      if (payload.client_operation_id) {
        fd.append('client_operation_id', payload.client_operation_id);
      }
      fd.append('attachment', {
        uri: payload.attachment.uri,
        name: payload.attachment.name,
        type: payload.attachment.type,
      } as unknown as Blob);
      return apiRequest<Record<string, unknown>>(`/incidents/${incidentId}/responses`, {
        method: 'POST',
        formData: fd,
      });
    }

    return apiRequest<Record<string, unknown>>(`/incidents/${incidentId}/responses`, {
      method: 'POST',
      body: {
        action_summary: payload.action_summary,
        result_notes: payload.result_notes ?? null,
        client_operation_id: payload.client_operation_id ?? null,
      },
    });
  },

  submitResolution: (incidentId: number, summaryNotes?: string) =>
    apiRequest<Record<string, unknown>>(`/incidents/${incidentId}/resolution-submissions`, {
      method: 'POST',
      body: summaryNotes ? { summary_notes: summaryNotes } : {},
    }),

  approveVerification: (incidentId: number, reason?: string) =>
    apiRequest<Record<string, unknown>>(`/incidents/${incidentId}/verification/approve`, {
      method: 'POST',
      body: reason ? { reason } : {},
    }),

  rejectVerification: (incidentId: number, reason: string) =>
    apiRequest<Record<string, unknown>>(`/incidents/${incidentId}/verification/reject`, {
      method: 'POST',
      body: { reason },
    }),

  manualRecheck: (incidentId: number) =>
    apiRequest<Record<string, unknown>>(`/incidents/${incidentId}/verification/recheck`, {
      method: 'POST',
    }),
};

export const syncApi = {
  syncOperations: (operations: SyncOperationInput[]) =>
    apiRequest<{ results: SyncOperationResult[] }>('/sync/operations', {
      method: 'POST',
      body: { operations },
    }),
};

export const notificationsApi = {
  registerDevice: (platform: 'android' | 'ios', token: string) =>
    apiRequest<Record<string, unknown>>('/devices/register', {
      method: 'POST',
      body: { platform, token },
    }),

  list: (page = 1, perPage = 20) =>
    apiRequest<PaginatedResponse<NotificationItem>>('/notifications', {
      query: { page, per_page: perPage },
    }),
};

export const adminApi = {
  listRoles: () => apiRequest<RoleRef[]>('/roles'),

  listUsers: (params?: { role_code?: string; q?: string; region_id?: number }) =>
    apiRequest<{ items: UserProfile[]; total: number }>('/users', { query: params }),

  createUser: (payload: {
    username: string;
    full_name: string;
    email?: string | null;
    password: string;
    role_code: RoleCode;
    region_ids: number[];
    is_active?: boolean;
    is_on_duty?: boolean;
  }) =>
    apiRequest<UserProfile>('/users', {
      method: 'POST',
      body: payload,
    }),

  updateUser: (
    userId: number,
    payload: {
      full_name?: string;
      email?: string | null;
      password?: string;
      role_code?: RoleCode;
      region_ids?: number[];
      is_active?: boolean;
      is_on_duty?: boolean;
    },
  ) =>
    apiRequest<UserProfile>(`/users/${userId}`, {
      method: 'PATCH',
      body: payload,
    }),

  listRegions: (includeInactive = false) =>
    apiRequest<{ items: RegionRef[]; total: number }>('/regions', {
      query: { include_inactive: includeInactive ? 'true' : undefined },
    }),

  createRegion: (payload: { code: string; name: string }) =>
    apiRequest<RegionRef>('/regions', {
      method: 'POST',
      body: payload,
    }),

  updateRegion: (regionId: number, payload: { name?: string; is_active?: boolean }) =>
    apiRequest<RegionRef>(`/regions/${regionId}`, {
      method: 'PATCH',
      body: payload,
    }),

  getRecoveryPolicy: (schoolId: number) =>
    apiRequest<RecoveryPolicyDetail>(`/schools/${schoolId}/recovery-policy`),

  upsertRecoveryPolicy: (
    schoolId: number,
    payload: {
      required_sensor_ids: number[];
      required_consecutive_normal: number;
      recheck_interval_seconds: number;
      expected_status: 'online' | 'warning';
    },
  ) =>
    apiRequest<RecoveryPolicyDetail>(`/schools/${schoolId}/recovery-policy`, {
      method: 'PUT',
      body: payload,
    }),

  listAuditLogs: (params?: {
    entity_type?: string;
    action?: string;
    page?: number;
    per_page?: number;
  }) =>
    apiRequest<PaginatedResponse<AuditLogItem>>('/audit-logs', {
      query: params,
    }),
};
