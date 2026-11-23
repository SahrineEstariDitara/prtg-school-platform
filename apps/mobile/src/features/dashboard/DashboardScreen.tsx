import React, { useCallback, useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useAuth } from '../auth/AuthContext';
import { useSync } from '../offline-sync/SyncContext';
import { incidentsApi, monitoringApi } from '../../shared/api/endpoints';
import { getCacheEntry, setCacheEntry } from '../../shared/storage/localDb';
import { useAppForeground } from '../../shared/hooks/useAppForeground';
import { computeOnlinePercentage } from '../monitoring/helpers';
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
  ScreenContainer,
} from '../../shared/components';
import { colors, radius, spacing } from '../../shared/theme/tokens';
import type { IncidentSummary, MonitoringRegionSummary } from '../../shared/types/domain';
import { formatDateTime } from '../../shared/hooks/formatters';

const CACHE_KEY_DASHBOARD = 'dashboard_summary_v1';
const CACHE_KEY_PRIORITY_INCIDENTS = 'dashboard_priority_incidents_v1';

export function DashboardScreen() {
  const router = useRouter();
  const { user, capabilities } = useAuth();
  const { isOnline, pendingCount, conflictCount } = useSync();

  const [summary, setSummary] = useState<MonitoringRegionSummary | null>(null);
  const [priorityIncidents, setPriorityIncidents] = useState<IncidentSummary[]>([]);
  const [cachedAt, setCachedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const loadData = useCallback(
    async (isRefresh = false) => {
      if (isRefresh) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }
      setErrorMsg(null);

      try {
        const incidentQuery =
          capabilities.roleCode === 'teknisi'
            ? { assigned_to_me: true, per_page: 10 }
            : capabilities.canVerifyIncident
              ? { status: 'pending_verification', per_page: 10 }
              : capabilities.roleCode === 'koordinator_teknisi'
                ? { status: 'unassigned', per_page: 10 }
                : { per_page: 10 };

        const [sumRes, incRes] = await Promise.all([
          monitoringApi.summaryByRegion(),
          incidentsApi.list(incidentQuery),
        ]);
        setSummary(sumRes);
        setPriorityIncidents(incRes.items);
        setCachedAt(null);

        await Promise.all([
          setCacheEntry(CACHE_KEY_DASHBOARD, sumRes),
          setCacheEntry(CACHE_KEY_PRIORITY_INCIDENTS, incRes.items),
        ]);
      } catch (err) {
        const [cachedSum, cachedInc] = await Promise.all([
          getCacheEntry<MonitoringRegionSummary>(CACHE_KEY_DASHBOARD),
          getCacheEntry<IncidentSummary[]>(CACHE_KEY_PRIORITY_INCIDENTS),
        ]);
        if (cachedSum) {
          setSummary(cachedSum.data);
          setPriorityIncidents(cachedInc?.data ?? []);
          setCachedAt(cachedSum.updatedAt);
        } else {
          setErrorMsg(err instanceof Error ? err.message : 'Gagal memuat ringkasan monitoring.');
        }
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [capabilities.roleCode, capabilities.canVerifyIncident],
  );

  useEffect(() => {
    loadData(false);
  }, [loadData]);

  useAppForeground(() => {
    if (isOnline) {
      loadData(true);
    }
  });

  if (loading && !summary) {
    return <LoadingState message="Memuat dashboard monitoring..." />;
  }

  if (errorMsg && !summary) {
    return <ErrorState message={errorMsg} onRetry={() => loadData(false)} />;
  }

  const onlinePct = summary ? computeOnlinePercentage(summary.counts, summary.total_schools) : 0;
  const priorityTitle =
    capabilities.roleCode === 'teknisi'
      ? 'Tugas Aktif Saya'
      : capabilities.canVerifyIncident
        ? 'Antrean Verifikasi Supervisor'
        : capabilities.roleCode === 'koordinator_teknisi'
          ? 'Incident Belum Ditugaskan (Unassigned)'
          : 'Incident Terbaru di Scope';

  return (
    <ScreenContainer refreshing={refreshing} onRefresh={() => loadData(true)}>
      <OfflineBanner isOnline={isOnline} pendingCount={pendingCount} />

      <View style={styles.headerRow}>
        <View style={styles.headerText}>
          <AppText variant="h2">Halo, {user?.full_name ?? 'Pengguna'}</AppText>
          <AppText variant="caption" color={colors.textSecondary}>
            {capabilities.displayName} • Scope:{' '}
            {capabilities.isGlobalScope
              ? 'Semua Wilayah DIY'
              : (user?.regions ?? []).map((r) => r.name).join(', ') || 'Regional'}
          </AppText>
        </View>
        <AppButton
          label="Inbox"
          variant="secondary"
          icon="notifications-outline"
          onPress={() => router.push('/notifications')}
        />
      </View>

      {cachedAt ? (
        <AppText variant="caption" color={colors.amber}>
          Menampilkan snapshot cache lokal ({formatDateTime(cachedAt)})
        </AppText>
      ) : null}

      {summary ? (
        <>
          <FreshnessBanner
            lastCheckedAt={summary.last_checked_at}
            isStale={summary.stale_schools > 0}
          />

          <View style={styles.kpiGrid}>
            <Card style={[styles.kpiCard, styles.kpiOffline]}>
              <AppText variant="captionBold" color={colors.offlineText}>
                Jaringan Offline
              </AppText>
              <AppText variant="h1" color={colors.offlineText}>
                {summary.counts.offline}
              </AppText>
              <AppText variant="caption" color={colors.textSecondary}>
                Dari {summary.total_schools} sekolah
              </AppText>
            </Card>

            <Card style={[styles.kpiCard, styles.kpiIncident]}>
              <AppText variant="captionBold" color={colors.purpleText}>
                Incident Aktif
              </AppText>
              <AppText variant="h1" color={colors.purpleText}>
                {summary.active_incidents}
              </AppText>
              <AppText variant="caption" color={colors.textSecondary}>
                Terpisah dari status PRTG
              </AppText>
            </Card>

            <Card style={[styles.kpiCard, styles.kpiOnline]}>
              <AppText variant="captionBold" color={colors.onlineText}>
                Jaringan Online
              </AppText>
              <AppText variant="h1" color={colors.onlineText}>
                {summary.counts.online}
              </AppText>
              <AppText variant="caption" color={colors.textSecondary}>
                {onlinePct}% normal
              </AppText>
            </Card>

            <Card style={[styles.kpiCard, styles.kpiWarn]}>
              <AppText variant="captionBold" color={colors.warningText}>
                Warning / Unknown
              </AppText>
              <AppText variant="h1" color={colors.warningText}>
                {summary.counts.warning} / {summary.counts.unknown}
              </AppText>
              <AppText variant="caption" color={colors.textSecondary}>
                Stale: {summary.stale_schools}
              </AppText>
            </Card>
          </View>
        </>
      ) : null}

      {conflictCount > 0 ? (
        <Card
          style={styles.conflictCard}
          onPress={() => router.push('/offline-queue')}
        >
          <AppText variant="bodyBold" color={colors.offlineText}>
            {conflictCount} Draft Offline Mengalami Konflik
          </AppText>
          <AppText variant="caption" color={colors.textSecondary}>
            Ketuk untuk meninjau & rekonsiliasi tanpa kehilangan catatan lapangan.
          </AppText>
        </Card>
      ) : null}

      <View style={styles.sectionHeader}>
        <AppText variant="h3">{priorityTitle}</AppText>
        <AppButton
          label="Semua"
          variant="ghost"
          onPress={() => router.push('/(tabs)/incidents')}
        />
      </View>

      {priorityIncidents.length === 0 ? (
        <EmptyState
          icon="checkmark-circle-outline"
          title="Tidak ada antrean prioritas saat ini"
          subtitle="Tarik ke bawah untuk memperbarui data terbaru dari server."
        />
      ) : (
        <View style={styles.listStack}>
          {priorityIncidents.slice(0, 5).map((inc) => (
            <Card
              key={inc.id}
              onPress={() => router.push(`/incidents/${inc.id}`)}
              style={styles.itemCard}
            >
              <View style={styles.itemTop}>
                <AppText variant="bodyBold" style={styles.flexOne}>
                  {inc.school.name}
                </AppText>
                <AppText variant="caption" color={colors.textMuted}>
                  #{inc.id}
                </AppText>
              </View>
              <AppText variant="caption" color={colors.textSecondary}>
                {inc.school.region.name} • {inc.affected_sensor_count} sensor terdampak
              </AppText>
              <View style={styles.badgeRow}>
                <NetworkStatusBadge status={inc.network_status} />
                <IncidentStatusBadge status={inc.incident_status} />
              </View>
            </Card>
          ))}
        </View>
      )}

      {summary && summary.by_region.length > 1 ? (
        <View style={styles.regionSection}>
          <AppText variant="h3">Ringkasan per Wilayah</AppText>
          {summary.by_region.map((bucket) => (
            <Card key={bucket.region.id} style={styles.regionCard}>
              <View style={styles.itemTop}>
                <AppText variant="bodyBold">{bucket.region.name}</AppText>
                <AppText variant="captionBold" color={colors.textSecondary}>
                  {bucket.total_schools} sekolah
                </AppText>
              </View>
              <View style={styles.regionStats}>
                <AppText variant="captionBold" color={colors.onlineText}>
                  Online: {bucket.counts.online}
                </AppText>
                <AppText variant="captionBold" color={colors.offlineText}>
                  Offline: {bucket.counts.offline}
                </AppText>
                <AppText variant="captionBold" color={colors.warningText}>
                  Warn: {bucket.counts.warning}
                </AppText>
                <AppText variant="captionBold" color={colors.purpleText}>
                  Incident: {bucket.active_incidents}
                </AppText>
              </View>
            </Card>
          ))}
        </View>
      ) : null}
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  headerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.md,
  },
  headerText: {
    flex: 1,
    gap: 2,
  },
  kpiGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
  },
  kpiCard: {
    width: '48.5%',
    gap: 4,
    padding: spacing.md,
  },
  kpiOffline: {
    borderColor: colors.offlineBorder,
    backgroundColor: '#FFF5F5',
  },
  kpiIncident: {
    borderColor: colors.purpleBorder,
    backgroundColor: '#FAF5FF',
  },
  kpiOnline: {
    borderColor: colors.onlineBorder,
    backgroundColor: '#F0FDF4',
  },
  kpiWarn: {
    borderColor: colors.warningBorder,
    backgroundColor: '#FFFBEB',
  },
  conflictCard: {
    borderColor: colors.offlineBorder,
    backgroundColor: colors.offlineBg,
    gap: 4,
  },
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: spacing.xs,
  },
  listStack: {
    gap: spacing.sm,
  },
  itemCard: {
    gap: spacing.xs,
    padding: spacing.md,
  },
  itemTop: {
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
    marginTop: 4,
  },
  regionSection: {
    gap: spacing.sm,
    marginTop: spacing.xs,
  },
  regionCard: {
    gap: spacing.xs,
    padding: spacing.md,
    borderRadius: radius.md,
  },
  regionStats: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.md,
  },
});
