import * as SQLite from 'expo-sqlite';
import type { OfflineDraftRecord, SyncStatus } from '../types/domain';

const DB_NAME = 'prtg_school_mobile.db';

let dbPromise: Promise<SQLite.SQLiteDatabase> | null = null;

async function getDb(): Promise<SQLite.SQLiteDatabase> {
  if (!dbPromise) {
    dbPromise = (async () => {
      const db = await SQLite.openDatabaseAsync(DB_NAME);
      await db.execAsync(`
        PRAGMA journal_mode = WAL;
        CREATE TABLE IF NOT EXISTS cache_entries (
          cache_key TEXT PRIMARY KEY NOT NULL,
          payload_json TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS offline_drafts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          client_operation_id TEXT UNIQUE NOT NULL,
          incident_id INTEGER NOT NULL,
          school_name TEXT NOT NULL,
          action_summary TEXT NOT NULL,
          result_notes TEXT,
          attachment_uri TEXT,
          attachment_mime TEXT,
          attachment_name TEXT,
          sync_status TEXT NOT NULL DEFAULT 'pending_sync',
          conflict_reason TEXT,
          server_response_id INTEGER,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_offline_drafts_status ON offline_drafts(sync_status);
        CREATE INDEX IF NOT EXISTS idx_offline_drafts_incident ON offline_drafts(incident_id);
      `);
      return db;
    })();
  }
  return dbPromise;
}

export async function setCacheEntry<T>(cacheKey: string, payload: T): Promise<void> {
  const db = await getDb();
  const now = new Date().toISOString();
  await db.runAsync(
    `INSERT OR REPLACE INTO cache_entries (cache_key, payload_json, updated_at) VALUES (?, ?, ?)`,
    cacheKey,
    JSON.stringify(payload),
    now,
  );
}

export async function getCacheEntry<T>(
  cacheKey: string,
): Promise<{ data: T; updatedAt: string } | null> {
  const db = await getDb();
  const row = await db.getFirstAsync<{ payload_json: string; updated_at: string }>(
    `SELECT payload_json, updated_at FROM cache_entries WHERE cache_key = ?`,
    cacheKey,
  );
  if (!row) return null;
  try {
    return {
      data: JSON.parse(row.payload_json) as T,
      updatedAt: row.updated_at,
    };
  } catch {
    return null;
  }
}

export async function clearAllCache(): Promise<void> {
  const db = await getDb();
  await db.runAsync(`DELETE FROM cache_entries`);
}

export interface CreateOfflineDraftInput {
  clientOperationId: string;
  incidentId: number;
  schoolName: string;
  actionSummary: string;
  resultNotes?: string | null;
  attachmentUri?: string | null;
  attachmentMime?: string | null;
  attachmentName?: string | null;
  syncStatus?: SyncStatus;
}

export async function insertOfflineDraft(
  input: CreateOfflineDraftInput,
): Promise<OfflineDraftRecord> {
  const db = await getDb();
  const now = new Date().toISOString();
  const status: SyncStatus = input.syncStatus ?? 'pending_sync';
  const result = await db.runAsync(
    `INSERT INTO offline_drafts (
      client_operation_id, incident_id, school_name, action_summary, result_notes,
      attachment_uri, attachment_mime, attachment_name, sync_status, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    input.clientOperationId,
    input.incidentId,
    input.schoolName,
    input.actionSummary,
    input.resultNotes ?? null,
    input.attachmentUri ?? null,
    input.attachmentMime ?? null,
    input.attachmentName ?? null,
    status,
    now,
    now,
  );

  return {
    id: result.lastInsertRowId,
    client_operation_id: input.clientOperationId,
    incident_id: input.incidentId,
    school_name: input.schoolName,
    action_summary: input.actionSummary,
    result_notes: input.resultNotes ?? null,
    attachment_uri: input.attachmentUri ?? null,
    attachment_mime: input.attachmentMime ?? null,
    attachment_name: input.attachmentName ?? null,
    sync_status: status,
    conflict_reason: null,
    server_response_id: null,
    created_at: now,
    updated_at: now,
  };
}

export async function listOfflineDrafts(incidentId?: number): Promise<OfflineDraftRecord[]> {
  const db = await getDb();
  if (typeof incidentId === 'number') {
    return db.getAllAsync<OfflineDraftRecord>(
      `SELECT * FROM offline_drafts WHERE incident_id = ? ORDER BY id DESC`,
      incidentId,
    );
  }
  return db.getAllAsync<OfflineDraftRecord>(`SELECT * FROM offline_drafts ORDER BY id DESC`);
}

export async function listPendingOfflineDrafts(): Promise<OfflineDraftRecord[]> {
  const db = await getDb();
  return db.getAllAsync<OfflineDraftRecord>(
    `SELECT * FROM offline_drafts WHERE sync_status IN ('draft', 'pending_sync', 'failed') ORDER BY id ASC`,
  );
}

export async function updateOfflineDraftStatus(params: {
  clientOperationId: string;
  syncStatus: SyncStatus;
  conflictReason?: string | null;
  serverResponseId?: number | null;
}): Promise<void> {
  const db = await getDb();
  const now = new Date().toISOString();
  await db.runAsync(
    `UPDATE offline_drafts
     SET sync_status = ?, conflict_reason = ?, server_response_id = COALESCE(?, server_response_id), updated_at = ?
     WHERE client_operation_id = ?`,
    params.syncStatus,
    params.conflictReason ?? null,
    params.serverResponseId ?? null,
    now,
    params.clientOperationId,
  );
}

export async function deleteOfflineDraft(clientOperationId: string): Promise<void> {
  const db = await getDb();
  await db.runAsync(
    `DELETE FROM offline_drafts WHERE client_operation_id = ?`,
    clientOperationId,
  );
}
