import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';
import { useRouter } from 'expo-router';
import { incidentsApi } from '../../shared/api/endpoints';
import { getCacheEntry, setCacheEntry } from '../../shared/storage/localDb';
import { useAuth } from '../auth/AuthContext';
import { useSync } from '../offline-sync/SyncContext';
import {
  AppText,
  Card,
  ChipFilter,
  EmptyState,
  ErrorState,
  IncidentStatusBadge,
  LoadingState,
  NetworkStatusBadge,
  OfflineBanner,
} from '../../shared/components';
import { colors, spacing } from '../../shared/theme/tokens';
import type { IncidentStatus, IncidentSummary } from '../../shared/types/domain';
import { formatDateTime } from '../../shared/hooks/formatters';

const STATUS_FILTERS: Array<{ value: '' | IncidentStatus; label: string }> = [
  { value: '', label: 'Semua' },
  { value: 'unassigned', label: 'Unassigned' },
  { value: 'assigned', label: 'Assigned' },
  { value: 'in_progress', label: 'In Progress' },
  { value: 'pending_verification', label: 'Pending Verif' },
  { value: 'awaiting_recovery', label: 'Awaiting Recheck' },
  { value: 'resolved', label: 'Resolved' },
];

const CACHE_KEY_INCIDENTS = 'incidents_list_default_v1';

interface IncidentRowProps {
  incident: IncidentSummary;
  onPress: (id: number) => void;
}

const IncidentRow = React.memo(function IncidentRow({ incident, onPress }: IncidentRowProps) {
  const handlePress = useCallback(() => onPress(incident.id), [onPress, incident.id]);

  return (
    <Card onPress={handlePress} style={styles.rowCard}>
      <View style={styles.rowBetween}>
        <AppText variant="bodyBold" style={styles.flexOne}>
          {incident.school.name}
        </AppText>
        <AppText variant="captionBold" color={colors.textMuted}>
          #{incident.id}
        </AppText>
      </View>
      <AppText variant="caption" color={colors.textSecondary}>
        {incident.school.region.name} • {incident.affected_sensor_count} sensor • Terdeteksi:{' '}
        {formatDateTime(incident.detected_at)}
      </AppText>
      <AppText variant="caption" color={colors.textSecondary}>
        Teknisi: {incident.current_assignee ? incident.current_assignee.full_name : 'Belum ditugaskan'}
      </AppText>
      <View style={styles.badgeRow}>
        <NetworkStatusBadge status={incident.network_status} />
        <IncidentStatusBadge status={incident.incident_status} />
      </View>
    </Card>
  );
});

export function IncidentsListScreen() {
  const router = useRouter();
  const { capabilities } = useAuth();
  const { isOnline, pendingCount } = useSync();

  const [statusFilter, setStatusFilter] = useState<'' | IncidentStatus>('');
  const [assignedToMe, setAssignedToMe] = useState(capabilities.roleCode === 'teknisi');
  const [items, setItems] = useState<IncidentSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const fetchIncidents = useCallback(
    async (nextPage: number, replace: boolean) => {
      if (replace && nextPage === 1) {
        setRefreshing(true);
      }
      setErrorMsg(null);
      try {
        const res = await incidentsApi.list({
          status: statusFilter || undefined,
          assigned_to_me: assignedToMe || undefined,
          page: nextPage,
          per_page: 25,
        });
        setTotal(res.pagination.total);
        setPage(nextPage);
        setItems((prev) => (replace ? res.items : [...prev, ...res.items]));
        if (nextPage === 1 && !statusFilter) {
          await setCacheEntry(CACHE_KEY_INCIDENTS, res.items);
        }
      } catch (err) {
        if (nextPage === 1) {
          const cached = await getCacheEntry<IncidentSummary[]>(CACHE_KEY_INCIDENTS);
          if (cached) {
            setItems(cached.data);
            setTotal(cached.data.length);
          } else {
            setErrorMsg(err instanceof Error ? err.message : 'Gagal memuat daftar incident.');
          }
        }
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [statusFilter, assignedToMe],
  );

  useEffect(() => {
    setLoading(true);
    fetchIncidents(1, true);
  }, [fetchIncidents]);

  const handleOpenIncident = useCallback(
    (id: number) => {
      router.push(`/incidents/${id}`);
    },
    [router],
  );

  const handleLoadMore = useCallback(() => {
    if (!loading && !refreshing && items.length < total) {
      fetchIncidents(page + 1, false);
    }
  }, [loading, refreshing, items.length, total, page, fetchIncidents]);

  const renderItem = useCallback(
    ({ item }: { item: IncidentSummary }) => (
      <IncidentRow incident={item} onPress={handleOpenIncident} />
    ),
    [handleOpenIncident],
  );

  const keyExtractor = useCallback((item: IncidentSummary) => String(item.id), []);

  return (
    <View style={styles.root}>
      <View style={styles.controls}>
        <OfflineBanner isOnline={isOnline} pendingCount={pendingCount} />
        {capabilities.roleCode === 'teknisi' ? (
          <View style={styles.toggleRow}>
            <ChipFilter
              label="Tugas Saya"
              selected={assignedToMe}
              onPress={() => setAssignedToMe(true)}
            />
            <ChipFilter
              label="Semua Wilayah Saya"
              selected={!assignedToMe}
              onPress={() => setAssignedToMe(false)}
            />
          </View>
        ) : null}
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.chipScroll}
        >
          {STATUS_FILTERS.map((f) => (
            <ChipFilter
              key={f.label}
              label={f.label}
              selected={statusFilter === f.value}
              onPress={() => setStatusFilter(f.value)}
            />
          ))}
        </ScrollView>
        <AppText variant="caption" color={colors.textSecondary}>
          Menampilkan {items.length} dari {total} incident
        </AppText>
      </View>

      {loading && items.length === 0 ? (
        <LoadingState message="Memuat daftar incident..." />
      ) : errorMsg && items.length === 0 ? (
        <ErrorState message={errorMsg} onRetry={() => fetchIncidents(1, true)} />
      ) : (
        <FlashList
          data={items}
          renderItem={renderItem}
          keyExtractor={keyExtractor}
          contentContainerStyle={styles.listContent}
          refreshing={refreshing}
          onRefresh={() => fetchIncidents(1, true)}
          onEndReached={handleLoadMore}
          onEndReachedThreshold={0.4}
          ListEmptyComponent={
            <EmptyState
              icon="checkmark-done-outline"
              title="Belum ada incident"
              subtitle="Tidak ada incident pada filter ini."
            />
          }
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  controls: {
    padding: spacing.lg,
    paddingBottom: spacing.sm,
    gap: spacing.sm,
    backgroundColor: colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  toggleRow: {
    flexDirection: 'row',
    gap: spacing.xs,
  },
  chipScroll: {
    gap: spacing.xs,
    paddingVertical: 2,
  },
  listContent: {
    padding: spacing.lg,
    paddingBottom: spacing.xxl,
  },
  rowCard: {
    marginBottom: spacing.sm,
    padding: spacing.md,
    gap: 4,
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
    marginTop: 4,
  },
});
