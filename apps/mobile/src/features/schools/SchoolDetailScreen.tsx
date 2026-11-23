import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useRouter } from 'expo-router';
import { monitoringApi } from '../../shared/api/endpoints';
import { useAuth } from '../auth/AuthContext';
import { sortSensorsBySeverity } from '../monitoring/helpers';
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
  ScreenContainer,
} from '../../shared/components';
import { colors, radius, spacing } from '../../shared/theme/tokens';
import type { IncidentSummary, SchoolDetail } from '../../shared/types/domain';
import { formatDateTime, formatDurationHours } from '../../shared/hooks/formatters';

export function SchoolDetailScreen({ schoolId }: { schoolId: number }) {
  const router = useRouter();
  const { capabilities } = useAuth();

  const [detail, setDetail] = useState<SchoolDetail | null>(null);
  const [schoolIncidents, setSchoolIncidents] = useState<IncidentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const loadDetail = useCallback(
    async (isRefresh = false) => {
      if (isRefresh) setRefreshing(true);
      else setLoading(true);
      setErrorMsg(null);
      try {
        const [d, incs] = await Promise.all([
          monitoringApi.schoolDetail(schoolId),
          monitoringApi.schoolIncidents(schoolId, 1, 15),
        ]);
        setDetail(d);
        setSchoolIncidents(incs.items);
      } catch (err) {
        setErrorMsg(err instanceof Error ? err.message : 'Gagal memuat detail sekolah.');
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [schoolId],
  );

  useEffect(() => {
    loadDetail(false);
  }, [loadDetail]);

  const flatSensors = useMemo(() => {
    if (!detail) return [];
    const list = detail.devices.flatMap((dev) =>
      dev.sensors.map((sn) => ({ ...sn, device_name: dev.raw_name })),
    );
    return sortSensorsBySeverity(list);
  }, [detail]);

  if (loading && !detail) {
    return <LoadingState message="Memuat detail sekolah..." />;
  }
  if (errorMsg && !detail) {
    return <ErrorState message={errorMsg} onRetry={() => loadDetail(false)} />;
  }
  if (!detail) {
    return <EmptyState title="Sekolah tidak ditemukan" />;
  }

  return (
    <ScreenContainer refreshing={refreshing} onRefresh={() => loadDetail(true)}>
      <Card style={styles.sectionCard}>
        <AppText variant="h2">{detail.name}</AppText>
        <AppText variant="caption" color={colors.textSecondary}>
          Wilayah: {detail.region.name} • {detail.device_count} device • {detail.sensor_count} sensor
        </AppText>
        <View style={styles.badgeRow}>
          <NetworkStatusBadge status={detail.network_status} />
          {detail.active_incident ? (
            <IncidentStatusBadge status={detail.active_incident.incident_status} />
          ) : null}
        </View>
      </Card>

      <FreshnessBanner
        lastCheckedAt={detail.last_checked_at}
        isStale={detail.is_stale}
        networkStatus={detail.network_status}
      />

      {detail.active_incident ? (
        <Card
          style={styles.activeIncidentCard}
          onPress={() => router.push(`/incidents/${detail.active_incident!.id}`)}
        >
          <View style={styles.rowBetween}>
            <AppText variant="h3" color={colors.offlineText}>
              Incident Aktif #{detail.active_incident.id}
            </AppText>
            <IncidentStatusBadge status={detail.active_incident.incident_status} />
          </View>
          <AppText variant="caption" color={colors.textSecondary}>
            Terdeteksi: {formatDateTime(detail.active_incident.detected_at)}
          </AppText>
          <AppButton
            label="Buka Detail Incident"
            icon="arrow-forward"
            onPress={() => router.push(`/incidents/${detail.active_incident!.id}`)}
          />
        </Card>
      ) : null}

      <Card style={styles.sectionCard}>
        <View style={styles.rowBetween}>
          <AppText variant="h3">Recovery Policy Sensor Wajib</AppText>
          {capabilities.canManageAdmin ? (
            <AppButton
              label="Atur Policy"
              variant="secondary"
              icon="settings-outline"
              onPress={() => router.push(`/schools/${detail.id}/policy`)}
            />
          ) : null}
        </View>
        {detail.recovery_policy && detail.recovery_policy.required_sensor_ids.length > 0 ? (
          <AppText variant="body" color={colors.textSecondary}>
            Versi v{detail.recovery_policy.version} •{' '}
            {detail.recovery_policy.required_sensor_ids.length} sensor wajib • Wajib{' '}
            {detail.recovery_policy.required_consecutive_normal}x normal berturut-turut (interval{' '}
            {detail.recovery_policy.recheck_interval_seconds} detik)
          </AppText>
        ) : (
          <AppText variant="captionBold" color={colors.warningText}>
            Recovery policy belum dikonfigurasi. Penutupan incident otomatis akan diblokir hingga
            Admin menentukan sensor wajib (FR-019).
          </AppText>
        )}
      </Card>

      <AppText variant="h3">Daftar Sensor PRTG ({flatSensors.length})</AppText>
      {flatSensors.length === 0 ? (
        <EmptyState title="Belum ada sensor PRTG tersinkron" />
      ) : (
        <View style={styles.stack}>
          {flatSensors.map((sn) => (
            <Card key={sn.id} style={styles.sensorCard}>
              <View style={styles.rowBetween}>
                <AppText variant="bodyBold" style={styles.flexOne}>
                  {sn.sensor_name}
                </AppText>
                {sn.is_required ? (
                  <View style={styles.reqPill}>
                    <AppText variant="badge" color={colors.primaryDark}>
                      WAJIB
                    </AppText>
                  </View>
                ) : null}
              </View>
              <AppText variant="caption" color={colors.textMuted}>
                Device: {sn.device_name ?? '-'} • Raw PRTG: {sn.raw_status ?? '-'}
              </AppText>
              <View style={styles.badgeRow}>
                <NetworkStatusBadge status={sn.normalized_status} />
                {sn.downtime_hours !== null ? (
                  <AppText variant="caption" color={colors.offlineText}>
                    Down: {formatDurationHours(sn.downtime_hours)}
                  </AppText>
                ) : null}
              </View>
              {sn.last_message ? (
                <AppText variant="caption" color={colors.textSecondary} numberOfLines={2}>
                  Pesan: {sn.last_message}
                </AppText>
              ) : null}
            </Card>
          ))}
        </View>
      )}

      <AppText variant="h3">Riwayat Incident Sekolah ({schoolIncidents.length})</AppText>
      {schoolIncidents.length === 0 ? (
        <EmptyState title="Belum ada riwayat incident" />
      ) : (
        <View style={styles.stack}>
          {schoolIncidents.map((inc) => (
            <Card
              key={inc.id}
              onPress={() => router.push(`/incidents/${inc.id}`)}
              style={styles.sensorCard}
            >
              <View style={styles.rowBetween}>
                <AppText variant="bodyBold">Incident #{inc.id}</AppText>
                <IncidentStatusBadge status={inc.incident_status} />
              </View>
              <AppText variant="caption" color={colors.textSecondary}>
                Terdeteksi: {formatDateTime(inc.detected_at)}
                {inc.resolved_at ? ` • Selesai: ${formatDateTime(inc.resolved_at)}` : ''}
              </AppText>
            </Card>
          ))}
        </View>
      )}
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  sectionCard: {
    gap: spacing.sm,
  },
  activeIncidentCard: {
    gap: spacing.sm,
    borderColor: colors.offlineBorder,
    backgroundColor: '#FFF5F5',
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
    alignItems: 'center',
    gap: spacing.xs,
  },
  stack: {
    gap: spacing.sm,
  },
  sensorCard: {
    padding: spacing.md,
    gap: 4,
  },
  reqPill: {
    backgroundColor: colors.primarySoft,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    borderRadius: radius.pill,
  },
});
