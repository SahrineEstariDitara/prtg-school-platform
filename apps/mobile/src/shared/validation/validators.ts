import type { RoleCode } from '../types/domain';

export const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024; // 5 MB (NFR-10 / FR-013)
export const ALLOWED_IMAGE_MIMES = ['image/jpeg', 'image/png', 'image/webp'] as const;

export interface ValidationResult {
  valid: boolean;
  message?: string;
}

export function validateLoginForm(username: string, password: string): ValidationResult {
  if (!username.trim()) {
    return { valid: false, message: 'Username wajib diisi.' };
  }
  if (!password) {
    return { valid: false, message: 'Password wajib diisi.' };
  }
  return { valid: true };
}

export function validateMandatoryReason(reason: string, label = 'Alasan'): ValidationResult {
  const clean = reason.trim();
  if (clean.length < 3) {
    return { valid: false, message: `${label} wajib diisi minimal 3 karakter.` };
  }
  return { valid: true };
}

export function validateResponseSummary(summary: string): ValidationResult {
  const clean = summary.trim();
  if (clean.length < 3) {
    return { valid: false, message: 'Ringkasan tindakan wajib diisi minimal 3 karakter.' };
  }
  return { valid: true };
}

export function validateAttachmentFile(params: {
  mimeType?: string | null;
  fileSize?: number | null;
}): ValidationResult {
  if (params.mimeType) {
    const normalized = params.mimeType.toLowerCase();
    const isAllowed = (ALLOWED_IMAGE_MIMES as readonly string[]).includes(normalized);
    if (!isAllowed) {
      return {
        valid: false,
        message: 'Format foto tidak didukung. Gunakan JPG, PNG, atau WebP.',
      };
    }
  }
  if (typeof params.fileSize === 'number' && params.fileSize > MAX_ATTACHMENT_BYTES) {
    return {
      valid: false,
      message: 'Ukuran foto melebihi batas maksimal 5 MB.',
    };
  }
  return { valid: true };
}

/**
 * Validasi aturan scope wilayah per role sesuai ADR-012:
 * - admin & supervisor_utama: global (region_ids boleh kosong)
 * - supervisor_wilayah & koordinator_teknisi: wajib tepat 1 wilayah
 * - teknisi: wajib minimal 1 wilayah (boleh multi-wilayah)
 */
export function validateRoleRegionScope(
  roleCode: RoleCode,
  regionIds: number[],
): ValidationResult {
  const uniqueIds = Array.from(new Set(regionIds));
  if (roleCode === 'supervisor_wilayah' || roleCode === 'koordinator_teknisi') {
    if (uniqueIds.length !== 1) {
      return {
        valid: false,
        message: `Role '${roleCode}' wajib memiliki tepat 1 wilayah tugas (ADR-012).`,
      };
    }
  }
  if (roleCode === 'teknisi') {
    if (uniqueIds.length < 1) {
      return {
        valid: false,
        message: "Role 'teknisi' wajib memiliki minimal 1 wilayah tugas (ADR-012).",
      };
    }
  }
  return { valid: true };
}
