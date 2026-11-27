import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { useAppForeground } from '../../shared/hooks/useAppForeground';
import { useNetworkStatus } from '../../shared/hooks/useNetworkStatus';
import { cleanupEvidencePhoto } from '../../shared/storage/evidenceStorage';
import { deleteOfflineDraft, listOfflineDrafts } from '../../shared/storage/localDb';
import type { OfflineDraftRecord } from '../../shared/types/domain';
import {
  processOfflineSyncQueue,
  queueResponseDraft,
  type CreateDraftParams,
  type SyncRunSummary,
} from './syncEngine';

export type SaveDraftInput = Omit<CreateDraftParams, 'userId'>;

export interface SyncContextValue {
  isOnline: boolean;
  drafts: OfflineDraftRecord[];
  pendingCount: number;
  conflictCount: number;
  isSyncing: boolean;
  lastSyncSummary: SyncRunSummary | null;
  reloadDrafts: () => Promise<void>;
  saveDraft: (params: SaveDraftInput) => Promise<OfflineDraftRecord>;
  triggerSyncNow: () => Promise<SyncRunSummary>;
  discardDraft: (clientOperationId: string) => Promise<void>;
}

const SyncContext = createContext<SyncContextValue | null>(null);

export function SyncProvider({ children }: { children: React.ReactNode }) {
  const { status, user, capabilities } = useAuth();
  const net = useNetworkStatus();
  const [drafts, setDrafts] = useState<OfflineDraftRecord[]>([]);
  const [isSyncing, setIsSyncing] = useState(false);
  const [lastSyncSummary, setLastSyncSummary] = useState<SyncRunSummary | null>(null);
  const syncingRef = useRef(false);

  const activeUserId = user?.id;

  const reloadDrafts = useCallback(async () => {
    if (typeof activeUserId !== 'number') {
      setDrafts([]);
      return;
    }
    try {
      const items = await listOfflineDrafts(undefined, activeUserId);
      setDrafts(items);
    } catch {
      // Abaikan jika SQLite belum siap
    }
  }, [activeUserId]);

  const triggerSyncNow = useCallback(async (): Promise<SyncRunSummary> => {
    if (
      syncingRef.current ||
      status !== 'authenticated' ||
      typeof activeUserId !== 'number' ||
      !capabilities.canUseOfflineDrafts
    ) {
      return { processed: 0, synced: 0, conflicts: 0, failed: 0 };
    }
    syncingRef.current = true;
    setIsSyncing(true);
    try {
      const summary = await processOfflineSyncQueue(activeUserId);
      setLastSyncSummary(summary);
      await reloadDrafts();
      return summary;
    } finally {
      syncingRef.current = false;
      setIsSyncing(false);
    }
  }, [status, activeUserId, capabilities.canUseOfflineDrafts, reloadDrafts]);

  useEffect(() => {
    if (status === 'authenticated' && typeof activeUserId === 'number') {
      reloadDrafts();
    } else {
      setDrafts([]);
      setLastSyncSummary(null);
    }
  }, [status, activeUserId, reloadDrafts]);

  // Auto-sync saat kembali online
  useEffect(() => {
    if (net.isInternetReachable && status === 'authenticated' && capabilities.canUseOfflineDrafts) {
      triggerSyncNow();
    }
  }, [net.isInternetReachable, status, capabilities.canUseOfflineDrafts, triggerSyncNow]);

  // Auto-sync ringan saat app kembali ke foreground (SAD §9.4)
  useAppForeground(() => {
    if (net.isInternetReachable && status === 'authenticated' && capabilities.canUseOfflineDrafts) {
      triggerSyncNow();
    }
  });

  const saveDraft = useCallback(
    async (params: SaveDraftInput) => {
      const created = await queueResponseDraft({
        ...params,
        userId: typeof activeUserId === 'number' ? activeUserId : 0,
      });
      await reloadDrafts();
      return created;
    },
    [activeUserId, reloadDrafts],
  );

  const discardDraft = useCallback(
    async (clientOperationId: string) => {
      const target = drafts.find((d) => d.client_operation_id === clientOperationId);
      await cleanupEvidencePhoto(target?.attachment_uri);
      await deleteOfflineDraft(clientOperationId);
      await reloadDrafts();
    },
    [drafts, reloadDrafts],
  );

  const pendingCount = useMemo(
    () =>
      drafts.filter(
        (d) =>
          d.sync_status === 'draft' ||
          d.sync_status === 'pending_sync' ||
          d.sync_status === 'failed',
      ).length,
    [drafts],
  );

  const conflictCount = useMemo(
    () => drafts.filter((d) => d.sync_status === 'conflict').length,
    [drafts],
  );

  const value = useMemo<SyncContextValue>(
    () => ({
      isOnline: net.isInternetReachable,
      drafts,
      pendingCount,
      conflictCount,
      isSyncing,
      lastSyncSummary,
      reloadDrafts,
      saveDraft,
      triggerSyncNow,
      discardDraft,
    }),
    [
      net.isInternetReachable,
      drafts,
      pendingCount,
      conflictCount,
      isSyncing,
      lastSyncSummary,
      reloadDrafts,
      saveDraft,
      triggerSyncNow,
      discardDraft,
    ],
  );

  return <SyncContext.Provider value={value}>{children}</SyncContext.Provider>;
}

export function useSync(): SyncContextValue {
  const ctx = useContext(SyncContext);
  if (!ctx) {
    throw new Error('useSync harus dipanggil di dalam SyncProvider.');
  }
  return ctx;
}
