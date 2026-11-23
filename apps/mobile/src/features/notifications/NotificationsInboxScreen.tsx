import React, { useCallback, useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';
import { useRouter } from 'expo-router';
import { notificationsApi } from '../../shared/api/endpoints';
import {
  AppText,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../shared/components';
import { colors, spacing } from '../../shared/theme/tokens';
import type { NotificationItem } from '../../shared/types/domain';
import { formatDateTime } from '../../shared/hooks/formatters';

interface NotifRowProps {
  item: NotificationItem;
  onPress: (incidentId: number | null) => void;
}

const NotifRow = React.memo(function NotifRow({ item, onPress }: NotifRowProps) {
  const handlePress = useCallback(() => onPress(item.incident_id), [onPress, item.incident_id]);

  return (
    <Card onPress={item.incident_id ? handlePress : undefined} style={styles.card}>
      <View style={styles.rowBetween}>
        <AppText variant="bodyBold" style={styles.flexOne}>
          {item.event_type}
        </AppText>
        <AppText variant="caption" color={colors.textMuted}>
          {item.provider_status}
        </AppText>
      </View>
      <AppText variant="caption" color={colors.textSecondary}>
        {item.incident_id ? `Incident #${item.incident_id} — Ketuk untuk membuka detail` : 'Notifikasi sistem'}
      </AppText>
      <AppText variant="caption" color={colors.textMuted}>
        {formatDateTime(item.created_at)}
      </AppText>
    </Card>
  );
});

export function NotificationsInboxScreen() {
  const router = useRouter();
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const loadInbox = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setErrorMsg(null);
    try {
      const res = await notificationsApi.list(1, 50);
      setItems(res.items);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Gagal memuat inbox notifikasi.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadInbox(false);
  }, [loadInbox]);

  const handleOpen = useCallback(
    (incidentId: number | null) => {
      if (incidentId) {
        router.push(`/incidents/${incidentId}`);
      }
    },
    [router],
  );

  const renderItem = useCallback(
    ({ item }: { item: NotificationItem }) => <NotifRow item={item} onPress={handleOpen} />,
    [handleOpen],
  );

  const keyExtractor = useCallback((item: NotificationItem) => String(item.id), []);

  if (loading && items.length === 0) {
    return <LoadingState message="Memuat notifikasi..." />;
  }
  if (errorMsg && items.length === 0) {
    return <ErrorState message={errorMsg} onRetry={() => loadInbox(false)} />;
  }

  return (
    <View style={styles.root}>
      <FlashList
        data={items}
        renderItem={renderItem}
        keyExtractor={keyExtractor}
        contentContainerStyle={styles.listContent}
        refreshing={refreshing}
        onRefresh={() => loadInbox(true)}
        ListEmptyComponent={
          <EmptyState
            icon="notifications-off-outline"
            title="Belum ada notifikasi"
            subtitle="Notifikasi incident baru, penugasan, dan hasil verifikasi akan tercatat di sini."
          />
        }
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
