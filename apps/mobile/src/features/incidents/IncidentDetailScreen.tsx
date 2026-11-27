import React, { useCallback, useEffect, useState } from 'react';
import { Alert, StyleSheet, View } from 'react-native';
import { useRouter } from 'expo-router';
import { incidentsApi } from '../../shared/api/endpoints';
import { ApiClientError } from '../../shared/api/client';
import { getCacheEntry, listOfflineDrafts, setCacheEntry } from '../../shared/storage/localDb';
import { useAuth } from '../auth/AuthContext';
import { useSync } from '../offline-sync/SyncContext';
import { canUserVerifyIncident } from '../../navigation/roleConfig';
import { VerificationPanel } from '../verification/VerificationPanel';
import {
  AppButton,
  AppText,
  Card,
  EmptyState,
  ErrorState,
  FreshnessBanner,
  IncidentStatusBadge,
  LoadingState,
  NetworkStatusBadge,
  OfflineBanner,
  PhotoPreview,
  ScreenContainer,
  SyncStatusBadge,
} from '../../shared/components';
import { colors, spacing } from '../../shared/theme/tokens';
import type {
  IncidentDetail,
  IncidentHistoryItem,
  OfflineDraftRecord,
} from '../../shared/types/domain';
import { formatDateTime } from '../../shared/hooks/formatters';

export function IncidentDetailScreen({ incidentId }: { incidentId: number }) {
  const router = useRouter();
  const { user, capabilities } = useAuth();
  const { isOnline, pendingCount } = useSync();

  const [detail, setDetail] = useState<IncidentDetail | null>(null);
  const [history, setHistory] = useState<IncidentHistoryItem[]>([]);
  const [localDrafts, setLocalDrafts] = useState<OfflineDraftRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const cacheKey = `incident_detail_${incidentId}`;

  const activeUserId = user?.id;

  const loadData = useCallback(
    async (isRefresh = false) => {
      if (isRefresh) setRefreshing(true);
      else setLoading(true);
      setErrorMsg(null);

      try {
        const [incDetail, incHist, drafts] = await Promise.all([
          incidentsApi.detail(incidentId),
          incidentsApi.history(incidentId),
          listOfflineDrafts(incidentId, activeUserId),
        ]);
        setDetail(incDetail);
        setHistory(incHist.items);
        setLocalDrafts(drafts);
        await setCacheEntry(cacheKey, { detail: incDetail, history: incHist.items });
      } catch (err) {
        const [cached, drafts] = await Promise.all([
          getCacheEntry<{ detail: IncidentDetail; history: IncidentHistoryItem[] }>(cacheKey),
          listOfflineDrafts(incidentId, activeUserId),
        ]);
        setLocalDrafts(drafts);
        if (cached) {
          setDetail(cached.data.detail);
          setHistory(cached.data.history);
        } else {
          setErrorMsg(err instanceof Error ? err.message : 'Gagal memuat detail incident.');
        }
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [incidentId, cacheKey, activeUserId],
  );

  useEffect(() => {
    loadData(false);
  }, [loadData]);

  const handleAcknowledge = async () => {
    if (!isOnline) {
      Alert.alert('Wajib Online', 'Acknowledge tugas mengubah status resmi dan wajib online (FR-012).');
      return;
    }
    setBusyAction('ack');
    try {
      await incidentsApi.acknowledge(incidentId);
      Alert.alert('Berhasil', 'Tugas telah Anda terima (acknowledged).');
      await loadData(true);
    } catch (err) {
      Alert.alert('Gagal', err instanceof ApiClientError ? err.message : 'Gagal acknowledge.');
    } finally {
      setBusyAction(null);
    }
  };

  const handleSubmitResolution = async () => {
    if (!isOnline) {
      Alert.alert(
        'Wajib Online',
        'Pengajuan penyelesaian (Resolution Submission) wajib dilakukan secara online (FR-016).',
      );
      return;
    }
    Alert.alert(
      'Ajukan Penyelesaian?',
      'Incident akan masuk tahap pending_verification untuk ditinjau Supervisor.',
      [
        { text: 'Batal', style: 'cancel' },
        {
          text: 'Ajukan',
          onPress: async () => {
            setBusyAction('submit');
            try {
              await incidentsApi.submitResolution(incidentId);
              Alert.alert('Terkirim', 'Incident kini menunggu verifikasi Supervisor.');
              await loadData(true);
            } catch (err) {
              Alert.alert(
                'Gagal Mengajukan',
                err instanceof ApiClientError ? err.message : 'Gagal mengajukan penyelesaian.',
              );
            } finally {
              setBusyAction(null);
            }
          },
        },
      ],
    );
  };

  if (loading && !detail) {
    return <LoadingState message="Memuat detail incident..." />;
  }
  if (errorMsg && !detail) {
    return <ErrorState message={errorMsg} onRetry={() => loadData(false)} />;
  }
  if (!detail) {
    return <EmptyState title="Incident tidak ditemukan" />;
  }

  const isAssignedToMe = Boolean(user && detail.current_assignee?.id === user.id);
  const canVerify = canUserVerifyIncident(user);
  const isNotResolved = detail.incident_status !== 'resolved';
  const canRespondNow =
    capabilities.canRespondIncident &&
    isAssignedToMe &&
    (detail.incident_status === 'acknowledged' || detail.incident_status === 'in_progress');
  const canAckNow =
    capabilities.canRespondIncident &&
    isAssignedToMe &&
    detail.incident_status === 'assigned';
  const canSubmitNow = canRespondNow && detail.responses.length > 0;

  return (
    <ScreenContainer refreshing={refreshing} onRefresh={() => loadData(true)}>
      <OfflineBanner isOnline={isOnline} pendingCount={pendingCount} />

      <Card style={styles.sectionCard}>
        <View style={styles.rowBetween}>
          <AppText variant="h2" style={styles.flexOne}>
            {detail.school.name}
          </AppText>
          <AppText variant="captionBold" color={colors.textMuted}>
            Incident #{detail.id}
          </AppText>
        </View>
        <AppText variant="caption" color={colors.textSecondary}>
          Wilayah: {detail.school.region.name} • Terdeteksi: {formatDateTime(detail.detected_at)}
        </AppText>
        <View style={styles.badgeRow}>
          <NetworkStatusBadge status={detail.network_status} />
          <IncidentStatusBadge status={detail.incident_status} />
        </View>
        <AppText variant="body" color={colors.textSecondary}>
          Teknisi Ditugaskan:{' '}
          <AppText variant="bodyBold">
            {detail.current_assignee ? detail.current_assignee.full_name : 'Belum ditugaskan'}
          </AppText>
        </AppText>
      </Card>

      <FreshnessBanner
        lastCheckedAt={detail.last_checked_at}
        isStale={detail.is_stale}
        networkStatus={detail.network_status}
      />

      {/* Aksi Kontekstual: Teknisi Assigned */}
      {canAckNow || canRespondNow ? (
        <Card style={styles.actionCard}>
          <AppText variant="h3">Aksi Teknisi Lapangan</AppText>
          {canAckNow ? (
            <AppButton
              label="Acknowledge Tugas (Online)"
              icon="hand-left"
              loading={busyAction === 'ack'}
              disabled={!isOnline}
              onPress={handleAcknowledge}
            />
          ) : null}
          {canRespondNow ? (
            <View style={styles.stack}>
              <AppButton
                label={isOnline ? 'Catat Tindakan & Foto Bukti' : 'Simpan Draft Tindakan (Offline)'}
                icon="create-outline"
                onPress={() =>
                  router.push({
                    pathname: `/incidents/${detail.id}/respond` as never,
                    params: { schoolName: detail.school.name },
                  })
                }
              />
              <AppButton
                label="Ajukan Penyelesaian (Online)"
                variant="success"
                icon="send"
                loading={busyAction === 'submit'}
                disabled={!isOnline || !canSubmitNow}
                onPress={handleSubmitResolution}
              />
              {!canSubmitNow ? (
                <AppText variant="caption" color={colors.textMuted}>
                  Minimal 1 catatan tindakan tersinkron diperlukan sebelum mengajukan penyelesaian.
                </AppText>
              ) : null}
            </View>
          ) : null}
        </Card>
      ) : null}

      {/* Aksi Kontekstual: Koordinator & Supervisor (Assign / Reassign) */}
      {capabilities.canManageAssignment && isNotResolved ? (
        <Card style={styles.actionCard}>
          <AppText variant="h3">Penugasan Teknisi</AppText>
          <AppButton
            label={detail.current_assignee ? 'Reassign Teknisi' : 'Tugaskan Teknisi Manual'}
            variant="secondary"
            icon="people-outline"
            disabled={!isOnline}
            onPress={() =>
              router.push({
                pathname: `/incidents/${detail.id}/assign` as never,
                params: { regionId: String(detail.school.region.id) },
              })
            }
          />
        </Card>
      ) : null}

      {/* Panel Verifikasi Khusus Supervisor Wilayah / Utama (Admin TIDAK pernah melihat ini) */}
      {canVerify ? (
        <VerificationPanel
          incident={detail}
          isOnline={isOnline}
          onUpdated={() => loadData(true)}
        />
      ) : null}

      {/* Draft Offline Lokal untuk Incident Ini */}
      {localDrafts.length > 0 ? (
        <Card style={styles.sectionCard}>
          <AppText variant="h3">Draft Offline di Perangkat ({localDrafts.length})</AppText>
          {localDrafts.map((dr) => (
            <View key={dr.client_operation_id} style={styles.subItem}>
              <SyncStatusBadge status={dr.sync_status} />
              <AppText variant="bodyBold">{dr.action_summary}</AppText>
              {dr.result_notes ? (
                <AppText variant="caption" color={colors.textSecondary}>
                  {dr.result_notes}
                </AppText>
              ) : null}
              {dr.conflict_reason ? (
                <AppText variant="captionBold" color={colors.offlineText}>
                  Catatan Server: {dr.conflict_reason}
                </AppText>
              ) : null}
            </View>
          ))}
        </Card>
      ) : null}

      {/* Sensor Terdampak */}
      <Card style={styles.sectionCard}>
        <AppText variant="h3">Sensor Terdampak ({detail.affected_sensors.length})</AppText>
        {detail.affected_sensors.length === 0 ? (
          <AppText variant="caption" color={colors.textMuted}>
            Tidak ada data sensor terdampak.
          </AppText>
        ) : (
          detail.affected_sensors.map((sn) => (
            <View key={sn.sensor_id} style={styles.subItem}>
              <View style={styles.rowBetween}>
                <AppText variant="bodyBold" style={styles.flexOne}>
                  {sn.sensor_name ?? `Sensor #${sn.sensor_id}`}
                </AppText>
                {sn.normalized_status ? (
                  <NetworkStatusBadge status={sn.normalized_status} />
                ) : null}
              </View>
              <AppText variant="caption" color={colors.textMuted}>
                Raw: {sn.raw_status ?? '-'} • Terakhir Down: {formatDateTime(sn.last_seen_down_at)}
              </AppText>
            </View>
          ))
        )}
      </Card>

      {/* Log Tindakan Teknisi */}
      <Card style={styles.sectionCard}>
        <AppText variant="h3">Catatan Tindakan Teknisi ({detail.responses.length})</AppText>
        {detail.responses.length === 0 ? (
          <AppText variant="caption" color={colors.textMuted}>
            Belum ada catatan tindakan teknisi.
          </AppText>
        ) : (
          detail.responses.map((resp) => (
            <View key={resp.id} style={styles.subItem}>
              <View style={styles.rowBetween}>
                <AppText variant="captionBold" color={colors.primaryDark}>
                  {resp.actor_name ?? `User #${resp.actor_id}`}
                </AppText>
                <AppText variant="caption" color={colors.textMuted}>
                  {formatDateTime(resp.created_at)}
                </AppText>
              </View>
              <AppText variant="bodyBold">{resp.action_summary}</AppText>
              {resp.result_notes ? (
                <AppText variant="body" color={colors.textSecondary}>
                  {resp.result_notes}
                </AppText>
              ) : null}
              {resp.has_attachment && resp.attachment_url ? (
                <PhotoPreview uri={resp.attachment_url} />
              ) : null}
            </View>
          ))
        )}
      </Card>

      {/* Riwayat Verifikasi & PRTG Recheck */}
      {detail.verifications.length > 0 || detail.recheck_logs.length > 0 ? (
        <Card style={styles.sectionCard}>
          <AppText variant="h3">Riwayat Verifikasi & Recheck PRTG</AppText>
          {detail.verifications.map((v) => (
            <View key={v.id} style={styles.subItem}>
              <AppText
                variant="bodyBold"
                color={v.decision === 'approved' ? colors.onlineText : colors.offlineText}
              >
                {v.decision === 'approved' ? 'Disetujui' : 'Ditolak'} oleh{' '}
                {v.supervisor_name ?? `Supervisor #${v.supervisor_id}`}
              </AppText>
              {v.reason ? (
                <AppText variant="caption" color={colors.textSecondary}>
                  Catatan: {v.reason}
                </AppText>
              ) : null}
              <AppText variant="caption" color={colors.textMuted}>
                {formatDateTime(v.created_at)}
              </AppText>
            </View>
          ))}
          {detail.recheck_logs.map((rc) => (
            <View key={rc.id} style={styles.subItem}>
              <AppText variant="captionBold">
                PRTG Recheck #{rc.check_sequence} —{' '}
                {rc.is_valid_response && rc.all_required_normal
                  ? `NORMAL (streak: ${rc.streak_after_check})`
                  : `BELUM PULIH / GAGAL (streak: ${rc.streak_after_check})`}
              </AppText>
              {rc.error_message ? (
                <AppText variant="caption" color={colors.offlineText}>
                  {rc.error_message}
                </AppText>
              ) : null}
              <AppText variant="caption" color={colors.textMuted}>
                {formatDateTime(rc.checked_at)}
              </AppText>
            </View>
          ))}
        </Card>
      ) : null}

      {/* Timeline Histori Lifecycle */}
      <Card style={styles.sectionCard}>
        <AppText variant="h3">Timeline Incident ({history.length})</AppText>
        {history.map((h) => (
          <View key={h.id} style={styles.subItem}>
            <AppText variant="captionBold">{h.event_type}</AppText>
            <AppText variant="caption" color={colors.textSecondary}>
              {h.from_status ?? '-'} {'->'} {h.to_status ?? '-'}
            </AppText>
            <AppText variant="caption" color={colors.textMuted}>
              {formatDateTime(h.created_at)}
            </AppText>
          </View>
        ))}
      </Card>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  sectionCard: {
    gap: spacing.sm,
  },
  actionCard: {
    gap: spacing.sm,
    borderColor: colors.infoBorder,
  },
  rowBetween: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.sm,
  },
  flexOne: {
    flex: 1,
  },
  badgeRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
  },
  stack: {
    gap: spacing.sm,
  },
  subItem: {
    paddingTop: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    gap: 4,
  },
});
