import React, { useCallback, useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useAuth } from '../auth/AuthContext';
import { incidentsApi } from '../../shared/api/endpoints';
import { OfflineQueueScreen } from '../offline-sync/OfflineQueueScreen';
import {
  AppButton,
  AppText,
  Card,
  EmptyState,
  ErrorState,
  IncidentStatusBadge,
  LoadingState,
  NetworkStatusBadge,
  ScreenContainer,
} from '../../shared/components';
import { colors, spacing } from '../../shared/theme/tokens';
import type { IncidentSummary, TechnicianWorkloadItem } from '../../shared/types/domain';
import { formatDateTime } from '../../shared/hooks/formatters';

/**
 * Tab Queue adaptif per role:
 * - Teknisi -> Offline Draft & Sync Queue
 * - Supervisor Wilayah / Utama -> Antrean Verifikasi (`pending_verification` + `awaiting_recovery`)
 * - Koordinator Teknisi -> Antrean `unassigned` + ringkasan beban aktif tim teknisi
 */
export function QueueScreen() {
  const router = useRouter();
  const { capabilities } = useAuth();

  if (capabilities.canUseOfflineDrafts) {
    return <OfflineQueueScreen />;
  }

  return <OperationsQueueContent />;
}

function OperationsQueueContent() {
  const router = useRouter();
  const { capabilities } = useAuth();

  const [primaryItems, setPrimaryItems] = useState<IncidentSummary[]>([]);
  const [secondaryItems, setSecondaryItems] = useState<IncidentSummary[]>([]);
  const [techs, setTechs] = useState<TechnicianWorkloadItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const loadQueue = useCallback(
    async (isRefresh = false) => {
      if (isRefresh) setRefreshing(true);
      else setLoading(true);
      setErrorMsg(null);
      try {
        if (capabilities.canVerifyIncident) {
          const [pendingVerif, awaitingRec] = await Promise.all([
            incidentsApi.list({ status: 'pending_verification', per_page: 50 }),
            incidentsApi.list({ status: 'awaiting_recovery', per_page: 50 }),
          ]);
          setPrimaryItems(pendingVerif.items);
          setSecondaryItems(awaitingRec.items);
        } else if (capabilities.canManageAssignment) {
          const [unassignedRes, techRes] = await Promise.all([
            incidentsApi.list({ status: 'unassigned', per_page: 50 }),
            incidentsApi.listTechnicians(),
          ]);
          setPrimaryItems(unassignedRes.items);
          setTechs(techRes.items);
        }
      } catch (err) {
        setErrorMsg(err instanceof Error ? err.message : 'Gagal memuat antrean operasional.');
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [capabilities.canVerifyIncident, capabilities.canManageAssignment],
  );

  useEffect(() => {
    loadQueue(false);
  }, [loadQueue]);

  if (loading && primaryItems.length === 0 && techs.length === 0) {
    return <LoadingState message="Memuat antrean..." />;
  }
  if (errorMsg && primaryItems.length === 0 && techs.length === 0) {
    return <ErrorState message={errorMsg} onRetry={() => loadQueue(false)} />;
  }

  if (capabilities.canVerifyIncident) {
    return (
      <ScreenContainer refreshing={refreshing} onRefresh={() => loadQueue(true)}>
        <AppText variant="h2">Menunggu Verifikasi ({primaryItems.length})</AppText>
        {primaryItems.length === 0 ? (
          <EmptyState
            icon="shield-checkmark-outline"
            title="Antrean verifikasi kosong"
            subtitle="Semua laporan penyelesaian teknisi telah ditinjau."
          />
        ) : (
          primaryItems.map((inc) => (
            <Card
              key={inc.id}
              onPress={() => router.push(`/incidents/${inc.id}`)}
              style={styles.card}
            >
              <AppText variant="bodyBold">{inc.school.name}</AppText>
              <AppText variant="caption" color={colors.textSecondary}>
                {inc.school.region.name} • Diajukan: {formatDateTime(inc.submitted_at)}
              </AppText>
              <View style={styles.badgeRow}>
                <NetworkStatusBadge status={inc.network_status} />
                <IncidentStatusBadge status={inc.incident_status} />
              </View>
            </Card>
          ))
        )}

        <AppText variant="h2" style={styles.sectionTop}>
          Menunggu Recheck PRTG ({secondaryItems.length})
        </AppText>
        {secondaryItems.length === 0 ? (
          <AppText variant="caption" color={colors.textMuted}>
            Tidak ada incident dalam tahap awaiting_recovery.
          </AppText>
        ) : (
          secondaryItems.map((inc) => (
            <Card
              key={inc.id}
              onPress={() => router.push(`/incidents/${inc.id}`)}
              style={styles.card}
            >
              <AppText variant="bodyBold">{inc.school.name}</AppText>
              <AppText variant="caption" color={colors.warningText}>
                Streak stabil saat ini: {inc.recovery_stable_streak}
              </AppText>
              <View style={styles.badgeRow}>
                <NetworkStatusBadge status={inc.network_status} />
                <IncidentStatusBadge status={inc.incident_status} />
              </View>
            </Card>
          ))
        )}
      </ScreenContainer>
    );
  }

  return (
    <ScreenContainer refreshing={refreshing} onRefresh={() => loadQueue(true)}>
      <AppText variant="h2">Antrean Unassigned ({primaryItems.length})</AppText>
      {primaryItems.length === 0 ? (
        <EmptyState
          icon="checkmark-circle-outline"
          title="Tidak ada incident unassigned"
          subtitle="Seluruh incident di wilayah Anda sudah memiliki teknisi."
        />
      ) : (
        primaryItems.map((inc) => (
          <Card key={inc.id} style={styles.card}>
            <AppText variant="bodyBold">{inc.school.name}</AppText>
            <AppText variant="caption" color={colors.textSecondary}>
              {inc.school.region.name} • Terdeteksi: {formatDateTime(inc.detected_at)}
            </AppText>
            <View style={styles.badgeRow}>
              <NetworkStatusBadge status={inc.network_status} />
              <IncidentStatusBadge status={inc.incident_status} />
            </View>
            <AppButton
              label="Tugaskan Teknisi"
              icon="person-add"
              onPress={() =>
                router.push({
                  pathname: `/incidents/${inc.id}/assign` as never,
                  params: { regionId: String(inc.school.region.id) },
                })
              }
            />
          </Card>
        ))
      )}

      <AppText variant="h2" style={styles.sectionTop}>
        Beban Aktif Tim Teknisi ({techs.length})
      </AppText>
      {techs.map((t) => (
        <Card key={t.id} style={styles.card}>
          <View style={styles.rowBetween}>
            <AppText variant="bodyBold">{t.full_name}</AppText>
            <AppText
              variant="captionBold"
              color={t.is_on_duty ? colors.onlineText : colors.textMuted}
            >
              {t.is_on_duty ? 'ON DUTY' : 'OFF DUTY'}
            </AppText>
          </View>
          <AppText variant="caption" color={colors.textSecondary}>
            Incident aktif: {t.active_workload} • Wilayah:{' '}
            {t.regions.map((r) => r.name).join(', ')}
          </AppText>
        </Card>
      ))}
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  card: {
    gap: spacing.xs,
    padding: spacing.md,
  },
  rowBetween: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  badgeRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
    marginTop: 4,
  },
  sectionTop: {
    marginTop: spacing.sm,
  },
});
