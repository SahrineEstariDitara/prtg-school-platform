import React, { useCallback, useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';
import { useRouter } from 'expo-router';
import { monitoringApi } from '../../shared/api/endpoints';
import { getCacheEntry, setCacheEntry } from '../../shared/storage/localDb';
import { useDebouncedValue } from '../../shared/hooks/useDebouncedValue';
import { useSync } from '../offline-sync/SyncContext';
import {
  AppInput,
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
import type { NetworkStatus, SchoolSummary } from '../../shared/types/domain';
import { formatDateTime } from '../../shared/hooks/formatters';

const STATUS_FILTERS: Array<{ value: '' | NetworkStatus; label: string }> = [
  { value: '', label: 'Semua' },
  { value: 'offline', label: 'Offline' },
  { value: 'warning', label: 'Warning' },
  { value: 'unknown', label: 'Unknown' },
  { value: 'online', label: 'Online' },
];

const CACHE_KEY_SCHOOLS = 'schools_list_default_v1';

interface SchoolRowProps {
  school: SchoolSummary;
  onPress: (id: number) => void;
}

const SchoolRow = React.memo(function SchoolRow({ school, onPress }: SchoolRowProps) {
  const handlePress = useCallback(() => onPress(school.id), [onPress, school.id]);

  return (
    <Card onPress={handlePress} style={styles.rowCard}>
      <View style={styles.rowHeader}>
        <AppText variant="bodyBold" style={styles.flexOne}>
          {school.name}
        </AppText>
        <AppText variant="caption" color={colors.textMuted}>
          {school.sensor_count} sensor
        </AppText>
      </View>
      <AppText variant="caption" color={colors.textSecondary}>
        {school.region.name} • Cek PRTG: {formatDateTime(school.last_checked_at)}
      </AppText>
      <View style={styles.badgeRow}>
        <NetworkStatusBadge status={school.network_status} />
        {school.active_incident ? (
          <IncidentStatusBadge status={school.active_incident.incident_status} />
        ) : null}
        {school.is_stale ? (
          <AppText variant="badge" color={colors.warningText} style={styles.staleTag}>
            DATA STALE
          </AppText>
        ) : null}
      </View>
    </Card>
  );
});

export function SchoolsListScreen() {
  const router = useRouter();
  const { isOnline, pendingCount } = useSync();

  const [search, setSearch] = useState('');
  const debouncedSearch = useDebouncedValue(search, 300);
  const [statusFilter, setStatusFilter] = useState<'' | NetworkStatus>('');
  const [schools, setSchools] = useState<SchoolSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const fetchSchools = useCallback(
    async (nextPage: number, replace: boolean) => {
      if (replace && nextPage === 1) {
        setRefreshing(true);
      }
      setErrorMsg(null);
      try {
        const res = await monitoringApi.listSchools({
          q: debouncedSearch || undefined,
          network_status: statusFilter || undefined,
          page: nextPage,
          per_page: 30,
        });
        setTotal(res.pagination.total);
        setPage(nextPage);
        setSchools((prev) => (replace ? res.items : [...prev, ...res.items]));
        if (nextPage === 1 && !debouncedSearch && !statusFilter) {
          await setCacheEntry(CACHE_KEY_SCHOOLS, res.items);
        }
      } catch (err) {
        if (nextPage === 1) {
          const cached = await getCacheEntry<SchoolSummary[]>(CACHE_KEY_SCHOOLS);
          if (cached) {
            setSchools(cached.data);
            setTotal(cached.data.length);
          } else {
            setErrorMsg(err instanceof Error ? err.message : 'Gagal memuat daftar sekolah.');
          }
        }
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [debouncedSearch, statusFilter],
  );

  useEffect(() => {
    setLoading(true);
    fetchSchools(1, true);
  }, [fetchSchools]);

  const handleOpenSchool = useCallback(
    (schoolId: number) => {
      router.push(`/schools/${schoolId}`);
    },
    [router],
  );

  const handleLoadMore = useCallback(() => {
    if (!loading && !refreshing && schools.length < total) {
      fetchSchools(page + 1, false);
    }
  }, [loading, refreshing, schools.length, total, page, fetchSchools]);

  const renderItem = useCallback(
    ({ item }: { item: SchoolSummary }) => (
      <SchoolRow school={item} onPress={handleOpenSchool} />
    ),
    [handleOpenSchool],
  );

  const keyExtractor = useCallback((item: SchoolSummary) => String(item.id), []);

  return (
    <View style={styles.root}>
      <View style={styles.controls}>
        <OfflineBanner isOnline={isOnline} pendingCount={pendingCount} />
        <AppInput
          placeholder="Cari nama sekolah..."
          value={search}
          onChangeText={setSearch}
          autoCorrect={false}
        />
        <View style={styles.chipRow}>
          {STATUS_FILTERS.map((f) => (
            <ChipFilter
              key={f.label}
              label={f.label}
              selected={statusFilter === f.value}
              onPress={() => setStatusFilter(f.value)}
            />
          ))}
        </View>
        <AppText variant="caption" color={colors.textSecondary}>
          Menampilkan {schools.length} dari {total} sekolah
        </AppText>
      </View>

      {loading && schools.length === 0 ? (
        <LoadingState message="Memuat daftar sekolah..." />
      ) : errorMsg && schools.length === 0 ? (
        <ErrorState message={errorMsg} onRetry={() => fetchSchools(1, true)} />
      ) : (
        <FlashList
          data={schools}
          renderItem={renderItem}
          keyExtractor={keyExtractor}
          contentContainerStyle={styles.listContent}
          refreshing={refreshing}
          onRefresh={() => fetchSchools(1, true)}
          onEndReached={handleLoadMore}
          onEndReachedThreshold={0.4}
          ListEmptyComponent={
            <EmptyState
              icon="school-outline"
              title="Sekolah tidak ditemukan"
              subtitle="Coba ubah kata kunci pencarian atau filter status."
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
  chipRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
  },
  listContent: {
    padding: spacing.lg,
    paddingBottom: spacing.xxl,
  },
  rowCard: {
    marginBottom: spacing.sm,
    padding: spacing.md,
    gap: spacing.xs,
  },
  rowHeader: {
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
    marginTop: 4,
  },
  staleTag: {
    marginLeft: spacing.xs,
  },
});
