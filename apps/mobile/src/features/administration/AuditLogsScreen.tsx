import React, { useCallback, useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';
import { adminApi } from '../../shared/api/endpoints';
import {
  AppText,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../shared/components';
import { colors, spacing } from '../../shared/theme/tokens';
import type { AuditLogItem } from '../../shared/types/domain';
import { formatDateTime } from '../../shared/hooks/formatters';

const AuditRow = React.memo(function AuditRow({ item }: { item: AuditLogItem }) {
  return (
    <Card style={styles.card}>
      <View style={styles.rowBetween}>
        <AppText variant="bodyBold" style={styles.flexOne}>
          {item.action}
        </AppText>
        <AppText variant="caption" color={colors.textMuted}>
          #{item.id}
        </AppText>
      </View>
      <AppText variant="caption" color={colors.textSecondary}>
        Entity: {item.entity_type} #{item.entity_id ?? '-'} • Actor ID: {item.actor_id ?? 'System'}
      </AppText>
      <AppText variant="caption" color={colors.textMuted}>
        {formatDateTime(item.created_at)}
      </AppText>
    </Card>
  );
});

export function AuditLogsScreen() {
  const [items, setItems] = useState<AuditLogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const loadLogs = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setErrorMsg(null);
    try {
      const res = await adminApi.listAuditLogs({ page: 1, per_page: 50 });
      setItems(res.items);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Gagal memuat audit log.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadLogs(false);
  }, [loadLogs]);

  const renderItem = useCallback(
    ({ item }: { item: AuditLogItem }) => <AuditRow item={item} />,
    [],
  );
  const keyExtractor = useCallback((item: AuditLogItem) => String(item.id), []);

  if (loading && items.length === 0) {
    return <LoadingState message="Memuat audit log..." />;
  }
  if (errorMsg && items.length === 0) {
    return <ErrorState message={errorMsg} onRetry={() => loadLogs(false)} />;
  }

  return (
    <View style={styles.root}>
      <FlashList
        data={items}
        renderItem={renderItem}
        keyExtractor={keyExtractor}
        contentContainerStyle={styles.listContent}
        refreshing={refreshing}
        onRefresh={() => loadLogs(true)}
        ListEmptyComponent={<EmptyState title="Belum ada catatan audit log" />}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  listContent: {
    padding: spacing.lg,
    paddingBottom: spacing.xxl,
  },
  card: {
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
});
