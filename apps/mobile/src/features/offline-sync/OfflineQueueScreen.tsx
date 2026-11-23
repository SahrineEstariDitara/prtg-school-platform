import React, { useCallback } from 'react';
import { Alert, StyleSheet, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';
import { useRouter } from 'expo-router';
import { useSync } from './SyncContext';
import {
  AppButton,
  AppText,
  Card,
  EmptyState,
  OfflineBanner,
  PhotoPreview,
  SyncStatusBadge,
} from '../../shared/components';
import { colors, radius, spacing } from '../../shared/theme/tokens';
import type { OfflineDraftRecord } from '../../shared/types/domain';
import { formatDateTime } from '../../shared/hooks/formatters';

interface DraftRowProps {
  draft: OfflineDraftRecord;
  onOpenIncident: (incidentId: number) => void;
  onDiscard: (clientOperationId: string) => void;
}

const DraftRow = React.memo(function DraftRow({
  draft,
  onOpenIncident,
  onDiscard,
}: DraftRowProps) {
  const handleOpen = useCallback(
    () => onOpenIncident(draft.incident_id),
    [onOpenIncident, draft.incident_id],
  );
  const handleDiscard = useCallback(
    () => onDiscard(draft.client_operation_id),
    [onDiscard, draft.client_operation_id],
  );

  return (
    <Card style={[styles.card, draft.sync_status === 'conflict' ? styles.conflictBorder : null]}>
      <View style={styles.rowBetween}>
        <AppText variant="bodyBold" style={styles.flexOne}>
          {draft.school_name} (#{draft.incident_id})
        </AppText>
        <SyncStatusBadge status={draft.sync_status} />
      </View>

      <AppText variant="body">{draft.action_summary}</AppText>
      {draft.result_notes ? (
        <AppText variant="caption" color={colors.textSecondary}>
          {draft.result_notes}
        </AppText>
      ) : null}

      {draft.attachment_uri ? (
        <PhotoPreview uri={draft.attachment_uri} isLocalFile height={120} />
      ) : null}

      {draft.conflict_reason ? (
        <View style={styles.conflictBox}>
          <AppText variant="captionBold" color={colors.offlineText}>
            Alasan Konflik / Catatan Server:
          </AppText>
          <AppText variant="caption" color={colors.offlineText}>
            {draft.conflict_reason}
          </AppText>
        </View>
      ) : null}

      <AppText variant="caption" color={colors.textMuted}>
        Op ID: {draft.client_operation_id} • Dibuat: {formatDateTime(draft.created_at)}
      </AppText>

      <View style={styles.actionRow}>
        <AppButton
          label="Buka Incident"
          variant="secondary"
          icon="open-outline"
          onPress={handleOpen}
          style={styles.flexOne}
        />
        {draft.sync_status === 'synced' || draft.sync_status === 'conflict' ? (
          <AppButton
            label={draft.sync_status === 'conflict' ? 'Hapus Setelah Tinjau' : 'Bersihkan'}
            variant="ghost"
            icon="trash-outline"
            onPress={handleDiscard}
          />
        ) : null}
      </View>
    </Card>
  );
});

export function OfflineQueueScreen() {
  const router = useRouter();
  const { isOnline, drafts, pendingCount, isSyncing, lastSyncSummary, triggerSyncNow, discardDraft } =
    useSync();

  const handleOpenIncident = useCallback(
    (incidentId: number) => {
      router.push(`/incidents/${incidentId}`);
    },
    [router],
  );

  const handleDiscard = useCallback(
    (clientOpId: string) => {
      Alert.alert(
        'Hapus Draft Lokal?',
        'Pastikan Anda sudah meninjau atau menyalin catatan penting bila berstatus konflik.',
        [
          { text: 'Batal', style: 'cancel' },
          {
            text: 'Hapus',
            style: 'destructive',
            onPress: () => {
              discardDraft(clientOpId);
            },
          },
        ],
      );
    },
    [discardDraft],
  );

  const renderItem = useCallback(
    ({ item }: { item: OfflineDraftRecord }) => (
      <DraftRow draft={item} onOpenIncident={handleOpenIncident} onDiscard={handleDiscard} />
    ),
    [handleOpenIncident, handleDiscard],
  );

  const keyExtractor = useCallback((item: OfflineDraftRecord) => item.client_operation_id, []);

  return (
    <View style={styles.root}>
      <View style={styles.headerBox}>
        <OfflineBanner isOnline={isOnline} pendingCount={pendingCount} />
        <View style={styles.rowBetween}>
          <View style={styles.flexOne}>
            <AppText variant="h3">Antrean Draft Offline ({drafts.length})</AppText>
            <AppText variant="caption" color={colors.textSecondary}>
              Setiap draft memakai client_operation_id unik agar bebas duplikasi saat sync.
            </AppText>
          </View>
          <AppButton
            label="Sync"
            icon="sync"
            loading={isSyncing}
            disabled={!isOnline || pendingCount === 0}
            onPress={() => {
              triggerSyncNow();
            }}
          />
        </View>
        {lastSyncSummary && lastSyncSummary.processed > 0 ? (
          <AppText variant="caption" color={colors.primaryDark}>
            Hasil sync terakhir: {lastSyncSummary.synced} tersinkron, {lastSyncSummary.conflicts}{' '}
            konflik, {lastSyncSummary.failed} gagal.
          </AppText>
        ) : null}
      </View>

      <FlashList
        data={drafts}
        renderItem={renderItem}
        keyExtractor={keyExtractor}
        contentContainerStyle={styles.listContent}
        ListEmptyComponent={
          <EmptyState
            icon="cloud-done-outline"
            title="Belum ada draft offline"
            subtitle="Draft tindakan teknisi yang dibuat saat offline akan muncul di sini."
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
  headerBox: {
    padding: spacing.lg,
    gap: spacing.sm,
    backgroundColor: colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  listContent: {
    padding: spacing.lg,
    paddingBottom: spacing.xxl,
  },
  card: {
    marginBottom: spacing.sm,
    padding: spacing.md,
    gap: spacing.xs,
  },
  conflictBorder: {
    borderColor: colors.offlineBorder,
    backgroundColor: '#FFF5F5',
  },
  conflictBox: {
    backgroundColor: colors.offlineBg,
    borderRadius: radius.sm,
    padding: spacing.sm,
    gap: 2,
  },
  rowBetween: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.sm,
  },
  actionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    marginTop: spacing.xs,
  },
  flexOne: {
    flex: 1,
  },
});
