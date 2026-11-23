/**
 * Tipe domain 1:1 dengan Shared Backend REST API v1 (/api/v1/*) & ADR-001..ADR-016.
 */

export type RoleCode =
  | 'admin'
  | 'supervisor_utama'
  | 'supervisor_wilayah'
  | 'koordinator_teknisi'
  | 'teknisi';

export type NetworkStatus = 'online' | 'offline' | 'warning' | 'unknown';

/** 8 status kanonis incident (ADR-007 — selalu gunakan `awaiting_recovery`). */
export type IncidentStatus =
  | 'detected'
  | 'unassigned'
  | 'assigned'
  | 'acknowledged'
  | 'in_progress'
  | 'pending_verification'
  | 'awaiting_recovery'
  | 'resolved';

/** 5 status sinkronisasi lokal offline (FR-014, ADR-013). */
export type SyncStatus = 'draft' | 'pending_sync' | 'synced' | 'conflict' | 'failed';

export interface RegionRef {
  id: number;
  code: string;
  name: string;
  is_active?: boolean;
}

export interface RoleRef {
  id: number;
  code: RoleCode;
  name: string;
  description?: string | null;
}

export interface UserProfile {
  id: number;
  username: string;
  email: string | null;
  full_name: string;
  is_active: boolean;
  is_on_duty: boolean;
  role: RoleRef | null;
  permissions: string[];
  scope_type: 'global' | 'regional';
  regions: RegionRef[];
}

export interface AuthTokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: UserProfile;
}

export interface NetworkCounts {
  online: number;
  offline: number;
  warning: number;
  unknown: number;
}

export interface DashboardSummary {
  total_schools: number;
  counts: NetworkCounts;
  stale_schools: number;
  active_incidents: number;
  last_checked_at: string | null;
}

export interface RegionMonitoringBucket {
  region: RegionRef;
  total_schools: number;
  counts: NetworkCounts;
  stale_schools: number;
  active_incidents: number;
}

export interface MonitoringRegionSummary extends DashboardSummary {
  by_region: RegionMonitoringBucket[];
}

export interface ActiveIncidentBrief {
  id: number;
  incident_status: IncidentStatus;
  current_assignee_id: number | null;
  detected_at: string | null;
}

export interface SchoolSummary {
  id: number;
  school_code: string | null;
  name: string;
  region: RegionRef;
  network_status: NetworkStatus;
  is_stale: boolean;
  last_checked_at: string | null;
  device_count: number;
  sensor_count: number;
  active_incident: ActiveIncidentBrief | null;
}

export interface PrtgSensorItem {
  id: number;
  prtg_sensor_id: number;
  sensor_name: string;
  raw_status: string | null;
  normalized_status: NetworkStatus;
  is_required: boolean;
  downtime_hours: number | null;
  uptime_hours: number | null;
  last_message: string | null;
  last_checked_at: string | null;
  is_stale: boolean;
  device_id?: number;
  prtg_device_id?: number;
  device_name?: string;
}

export interface PrtgDeviceItem {
  id: number;
  prtg_device_id: number;
  raw_name: string;
  ip_address: string | null;
  prtg_group_path: string | null;
  last_seen_at: string | null;
  sensors: PrtgSensorItem[];
}

export interface SchoolRecoveryPolicySummary {
  version: number;
  required_consecutive_normal: number;
  recheck_interval_seconds: number;
  required_sensor_ids: number[];
}

export interface SchoolDetail extends SchoolSummary {
  devices: PrtgDeviceItem[];
  recovery_policy: SchoolRecoveryPolicySummary | null;
}

export interface RecoveryPolicyDetail {
  school_id: number;
  configured: boolean;
  version: number;
  required_consecutive_normal: number;
  recheck_interval_seconds: number;
  required_sensors: Array<{
    sensor_id: number;
    prtg_sensor_id: number | null;
    sensor_name: string | null;
    expected_status: 'online' | 'warning';
  }>;
}

export interface AssigneeRef {
  id: number;
  username: string;
  full_name: string;
}

export interface IncidentSummary {
  id: number;
  school: {
    id: number;
    name: string;
    region: RegionRef;
  };
  network_status: NetworkStatus;
  is_stale: boolean;
  last_checked_at: string | null;
  incident_status: IncidentStatus;
  recovery_stable_streak: number;
  current_assignee: AssigneeRef | null;
  affected_sensor_count: number;
  detected_at: string | null;
  acknowledged_at: string | null;
  submitted_at: string | null;
  resolved_at: string | null;
}

export interface AffectedSensorItem {
  sensor_id: number;
  prtg_sensor_id: number | null;
  sensor_name: string | null;
  raw_status: string | null;
  normalized_status: NetworkStatus | null;
  first_detected_at: string | null;
  last_seen_down_at: string | null;
}

export interface IncidentAssignmentItem {
  id: number;
  technician_id: number;
  technician_name: string | null;
  assigned_by_id: number | null;
  assignment_type: string;
  reason: string | null;
  is_active: boolean;
  assigned_at: string | null;
  ended_at: string | null;
}

export interface ResponseLogItem {
  id: number;
  actor_id: number;
  actor_name: string | null;
  action_summary: string;
  result_notes: string | null;
  has_attachment: boolean;
  attachment_url: string | null;
  attachment_mime: string | null;
  attachment_size: number | null;
  client_operation_id: string | null;
  created_at: string | null;
}

export interface VerificationRecordItem {
  id: number;
  supervisor_id: number;
  supervisor_name: string | null;
  decision: 'approved' | 'rejected';
  reason: string | null;
  policy_version: number | null;
  created_at: string | null;
}

export interface PrtgRecheckLogItem {
  id: number;
  check_sequence: number;
  all_required_normal: boolean;
  is_valid_response: boolean;
  streak_after_check: number;
  error_message: string | null;
  snapshot: Record<string, unknown> | null;
  checked_at: string | null;
}

export interface IncidentDetail extends IncidentSummary {
  affected_sensors: AffectedSensorItem[];
  assignments: IncidentAssignmentItem[];
  responses: ResponseLogItem[];
  verifications: VerificationRecordItem[];
  recheck_logs: PrtgRecheckLogItem[];
  recovery_policy: {
    configured: boolean;
    version: number;
    required_consecutive_normal: number;
    recheck_interval_seconds: number;
    required_sensor_count: number;
  };
}

export interface IncidentHistoryItem {
  id: number;
  event_type: string;
  from_status: string | null;
  to_status: string | null;
  actor_id: number | null;
  metadata: Record<string, unknown> | null;
  request_id: string | null;
  created_at: string | null;
}

export interface TechnicianWorkloadItem {
  id: number;
  username: string;
  full_name: string;
  is_active: boolean;
  is_on_duty: boolean;
  active_workload: number;
  last_assigned_at: string | null;
  regions: RegionRef[];
}

export interface SyncOperationInput {
  client_operation_id: string;
  operation_type: 'response_log.create';
  payload: {
    incident_id: number;
    action_summary: string;
    result_notes?: string | null;
  };
}

export interface SyncOperationResult {
  client_operation_id: string;
  sync_status: 'synced' | 'conflict' | 'failed';
  deduplicated?: boolean;
  result?: { id?: number; [key: string]: unknown } | null;
  conflict_reason?: string | null;
  error?: string | null;
}

export interface NotificationItem {
  id: number;
  incident_id: number | null;
  event_type: string;
  dedup_key: string;
  provider: string;
  provider_status: string;
  provider_message_id: string | null;
  created_at: string | null;
}

export interface AuditLogItem {
  id: number;
  actor_id: number | null;
  action: string;
  entity_type: string;
  entity_id: number | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  request_id: string | null;
  created_at: string | null;
}

export interface OfflineDraftRecord {
  id: number;
  client_operation_id: string;
  incident_id: number;
  school_name: string;
  action_summary: string;
  result_notes: string | null;
  attachment_uri: string | null;
  attachment_mime: string | null;
  attachment_name: string | null;
  sync_status: SyncStatus;
  conflict_reason: string | null;
  server_response_id: number | null;
  created_at: string;
  updated_at: string;
}
