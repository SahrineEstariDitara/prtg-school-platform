import {
  MAX_ATTACHMENT_BYTES,
  validateAttachmentFile,
  validateLoginForm,
  validateMandatoryReason,
  validateRoleRegionScope,
} from '../src/shared/validation/validators';

describe('Client form validators (ADR-012 & FR-010/013/017)', () => {
  test('Login validator requires non-empty username and password', () => {
    expect(validateLoginForm('', 'secret').valid).toBe(false);
    expect(validateLoginForm('admin', '').valid).toBe(false);
    expect(validateLoginForm('admin', 'secret').valid).toBe(true);
  });

  test('Mandatory reason validator enforces minimum length', () => {
    expect(validateMandatoryReason('  ').valid).toBe(false);
    expect(validateMandatoryReason('ok').valid).toBe(false);
    expect(validateMandatoryReason('Teknisi sedang cuti').valid).toBe(true);
  });

  test('Attachment validator enforces JPG/PNG/WebP and 5 MB max limit', () => {
    expect(validateAttachmentFile({ mimeType: 'image/jpeg', fileSize: 1024 }).valid).toBe(true);
    expect(validateAttachmentFile({ mimeType: 'image/webp', fileSize: MAX_ATTACHMENT_BYTES }).valid).toBe(
      true,
    );
    expect(validateAttachmentFile({ mimeType: 'application/pdf', fileSize: 500 }).valid).toBe(
      false,
    );
    expect(
      validateAttachmentFile({ mimeType: 'image/png', fileSize: MAX_ATTACHMENT_BYTES + 1 }).valid,
    ).toBe(false);
  });

  test('Role-region validator strictly enforces ADR-012 rules', () => {
    // Supervisor Wilayah & Koordinator Teknisi: exact 1 region
    expect(validateRoleRegionScope('supervisor_wilayah', []).valid).toBe(false);
    expect(validateRoleRegionScope('supervisor_wilayah', [1, 2]).valid).toBe(false);
    expect(validateRoleRegionScope('supervisor_wilayah', [1]).valid).toBe(true);

    expect(validateRoleRegionScope('koordinator_teknisi', []).valid).toBe(false);
    expect(validateRoleRegionScope('koordinator_teknisi', [2, 3]).valid).toBe(false);
    expect(validateRoleRegionScope('koordinator_teknisi', [2]).valid).toBe(true);

    // Teknisi: >= 1 region (multi-region allowed)
    expect(validateRoleRegionScope('teknisi', []).valid).toBe(false);
    expect(validateRoleRegionScope('teknisi', [1]).valid).toBe(true);
    expect(validateRoleRegionScope('teknisi', [1, 2, 3]).valid).toBe(true);

    // Admin & Supervisor Utama: global (0 regions allowed)
    expect(validateRoleRegionScope('admin', []).valid).toBe(true);
    expect(validateRoleRegionScope('supervisor_utama', []).valid).toBe(true);
  });
});
