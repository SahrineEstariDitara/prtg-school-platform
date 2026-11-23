import {
  processOfflineSyncQueue,
  queueResponseDraft,
} from '../src/features/offline-sync/syncEngine';
import * as localDb from '../src/shared/storage/localDb';
import { syncApi } from '../src/shared/api/endpoints';
import type { OfflineDraftRecord } from '../src/shared/types/domain';

jest.mock('../src/shared/storage/localDb', () => ({
  insertOfflineDraft: jest.fn(),
  listPendingOfflineDrafts: jest.fn(),
  updateOfflineDraftStatus: jest.fn(),
}));

jest.mock('../src/shared/api/endpoints', () => ({
  syncApi: {
    syncOperations: jest.fn(),
  },
  incidentsApi: {
    createResponse: jest.fn(),
  },
}));

describe('Offline Sync Engine (FR-014, FR-015, ADR-013, TC-12)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('queueResponseDraft creates draft with unique client_operation_id', async () => {
    (localDb.insertOfflineDraft as jest.Mock).mockImplementation(async (input) => ({
      id: 1,
      client_operation_id: input.clientOperationId,
      incident_id: input.incidentId,
      school_name: input.schoolName,
      action_summary: input.actionSummary,
      result_notes: input.resultNotes ?? null,
      attachment_uri: null,
      attachment_mime: null,
      attachment_name: null,
      sync_status: 'pending_sync',
      conflict_reason: null,
      server_response_id: null,
      created_at: '2026-11-23T00:00:00Z',
      updated_at: '2026-11-23T00:00:00Z',
    }));

    const draft = await queueResponseDraft({
      incidentId: 55,
      schoolName: 'SMAN 1 Yogyakarta',
      actionSummary: 'Ganti patch cord FO',
    });

    expect(draft.client_operation_id).toMatch(/^op-/);
    expect(draft.sync_status).toBe('pending_sync');
    expect(localDb.insertOfflineDraft).toHaveBeenCalledTimes(1);
  });

  test('processOfflineSyncQueue marks synced items and retains conflict drafts without deletion', async () => {
    const drafts: OfflineDraftRecord[] = [
      {
        id: 1,
        client_operation_id: 'op-uuid-1',
        incident_id: 10,
        school_name: 'SMAN 1 Bantul',
        action_summary: 'Restart router mikrotik',
        result_notes: null,
        attachment_uri: null,
        attachment_mime: null,
        attachment_name: null,
        sync_status: 'pending_sync',
        conflict_reason: null,
        server_response_id: null,
        created_at: '2026-11-23T00:00:00Z',
        updated_at: '2026-11-23T00:00:00Z',
      },
      {
        id: 2,
        client_operation_id: 'op-uuid-2',
        incident_id: 11,
        school_name: 'SMKN 2 Depok',
        action_summary: 'Cek adaptor ONT',
        result_notes: 'Sudah diganti',
        attachment_uri: null,
        attachment_mime: null,
        attachment_name: null,
        sync_status: 'pending_sync',
        conflict_reason: null,
        server_response_id: null,
        created_at: '2026-11-23T00:01:00Z',
        updated_at: '2026-11-23T00:01:00Z',
      },
    ];

    (localDb.listPendingOfflineDrafts as jest.Mock).mockResolvedValue(drafts);
    (syncApi.syncOperations as jest.Mock).mockResolvedValue({
      results: [
        {
          client_operation_id: 'op-uuid-1',
          sync_status: 'synced',
          deduplicated: false,
          result: { id: 901 },
        },
        {
          client_operation_id: 'op-uuid-2',
          sync_status: 'conflict',
          conflict_reason: 'Incident sudah resolved di server.',
        },
      ],
    });

    const summary = await processOfflineSyncQueue();

    expect(summary).toEqual({
      processed: 2,
      synced: 1,
      conflicts: 1,
      failed: 0,
    });

    expect(localDb.updateOfflineDraftStatus).toHaveBeenCalledWith({
      clientOperationId: 'op-uuid-1',
      syncStatus: 'synced',
      conflictReason: null,
      serverResponseId: 901,
    });
    expect(localDb.updateOfflineDraftStatus).toHaveBeenCalledWith({
      clientOperationId: 'op-uuid-2',
      syncStatus: 'conflict',
      conflictReason: 'Incident sudah resolved di server.',
    });
  });
});
