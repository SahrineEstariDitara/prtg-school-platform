import { ApiClientError, generateClientId } from '../../shared/api/client';
import { incidentsApi, syncApi } from '../../shared/api/endpoints';
import {
  insertOfflineDraft,
  listPendingOfflineDrafts,
  updateOfflineDraftStatus,
} from '../../shared/storage/localDb';
import type { OfflineDraftRecord, SyncOperationInput } from '../../shared/types/domain';

export interface SyncRunSummary {
  processed: number;
  synced: number;
  conflicts: number;
  failed: number;
}

export interface CreateDraftParams {
  incidentId: number;
  schoolName: string;
  actionSummary: string;
  resultNotes?: string | null;
  attachmentUri?: string | null;
  attachmentMime?: string | null;
  attachmentName?: string | null;
}

/**
 * Menyimpan draft tindakan teknisi ke SQLite dengan client_operation_id UUID unik (FR-014, TC-12).
 */
export async function queueResponseDraft(params: CreateDraftParams): Promise<OfflineDraftRecord> {
  const clientOperationId = generateClientId('op');
  return insertOfflineDraft({
    clientOperationId,
    incidentId: params.incidentId,
    schoolName: params.schoolName,
    actionSummary: params.actionSummary.trim(),
    resultNotes: params.resultNotes?.trim() || null,
    attachmentUri: params.attachmentUri ?? null,
    attachmentMime: params.attachmentMime ?? null,
    attachmentName: params.attachmentName ?? null,
    syncStatus: 'pending_sync',
  });
}

/**
 * Memproses antrean draft offline secara idempoten ke server.
 * - Draft tanpa foto: dikirim batch ke POST /api/v1/sync/operations.
 * - Draft dengan foto lokal: dikirim ke POST /api/v1/incidents/{id}/responses (multipart)
 *   membawa client_operation_id yang sama agar tetap idempoten.
 * - Jika server mengembalikan conflict (incident closed/reassigned), draft TIDAK dihapus,
 *   melainkan ditandai `conflict` beserta pesan server untuk rekonsiliasi manual (ADR-013).
 */
export async function processOfflineSyncQueue(): Promise<SyncRunSummary> {
  const pending = await listPendingOfflineDrafts();
  if (pending.length === 0) {
    return { processed: 0, synced: 0, conflicts: 0, failed: 0 };
  }

  let synced = 0;
  let conflicts = 0;
  let failed = 0;

  const textOnlyDrafts = pending.filter((d) => !d.attachment_uri);
  const attachmentDrafts = pending.filter((d) => Boolean(d.attachment_uri));

  // 1. Batch sync untuk draft teks melalui /api/v1/sync/operations
  if (textOnlyDrafts.length > 0) {
    const operations: SyncOperationInput[] = textOnlyDrafts.map((d) => ({
      client_operation_id: d.client_operation_id,
      operation_type: 'response_log.create',
      payload: {
        incident_id: d.incident_id,
        action_summary: d.action_summary,
        result_notes: d.result_notes,
      },
    }));

    try {
      const response = await syncApi.syncOperations(operations);
      for (const item of response.results) {
        if (item.sync_status === 'synced') {
          await updateOfflineDraftStatus({
            clientOperationId: item.client_operation_id,
            syncStatus: 'synced',
            conflictReason: null,
            serverResponseId: typeof item.result?.id === 'number' ? item.result.id : null,
          });
          synced += 1;
        } else if (item.sync_status === 'conflict') {
          await updateOfflineDraftStatus({
            clientOperationId: item.client_operation_id,
            syncStatus: 'conflict',
            conflictReason:
              item.conflict_reason ||
              'Konflik state incident di server. Tinjau sebelum membuang draft.',
          });
          conflicts += 1;
        } else {
          await updateOfflineDraftStatus({
            clientOperationId: item.client_operation_id,
            syncStatus: 'failed',
            conflictReason:
              item.error || item.conflict_reason || 'Sinkronisasi gagal divalidasi server.',
          });
          failed += 1;
        }
      }
    } catch (err) {
      const msg =
        err instanceof ApiClientError ? err.message : 'Gagal menghubungi server saat sinkronisasi.';
      for (const d of textOnlyDrafts) {
        await updateOfflineDraftStatus({
          clientOperationId: d.client_operation_id,
          syncStatus: 'failed',
          conflictReason: msg,
        });
        failed += 1;
      }
    }
  }

  // 2. Upload per-item untuk draft berlampiran foto dengan client_operation_id idempoten
  for (const draft of attachmentDrafts) {
    try {
      const res = await incidentsApi.createResponse(draft.incident_id, {
        action_summary: draft.action_summary,
        result_notes: draft.result_notes,
        client_operation_id: draft.client_operation_id,
        attachment: draft.attachment_uri
          ? {
              uri: draft.attachment_uri,
              name: draft.attachment_name || `evidence-${draft.incident_id}.jpg`,
              type: draft.attachment_mime || 'image/jpeg',
            }
          : null,
      });
      await updateOfflineDraftStatus({
        clientOperationId: draft.client_operation_id,
        syncStatus: 'synced',
        conflictReason: null,
        serverResponseId: typeof res.id === 'number' ? res.id : null,
      });
      synced += 1;
    } catch (err) {
      if (err instanceof ApiClientError && (err.status === 409 || err.status === 403)) {
        await updateOfflineDraftStatus({
          clientOperationId: draft.client_operation_id,
          syncStatus: 'conflict',
          conflictReason: err.message,
        });
        conflicts += 1;
      } else {
        const msg = err instanceof ApiClientError ? err.message : 'Upload lampiran gagal.';
        await updateOfflineDraftStatus({
          clientOperationId: draft.client_operation_id,
          syncStatus: 'failed',
          conflictReason: msg,
        });
        failed += 1;
      }
    }
  }

  return {
    processed: pending.length,
    synced,
    conflicts,
    failed,
  };
}
