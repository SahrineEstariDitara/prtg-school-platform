import { Directory, File, Paths } from 'expo-file-system';

const EVIDENCE_DIR_NAME = 'prtg_evidence';

function inferExtension(uri: string, mimeType?: string | null): string {
  const mime = (mimeType || '').toLowerCase();
  if (mime === 'image/png') return '.png';
  if (mime === 'image/webp') return '.webp';
  if (mime === 'image/jpeg' || mime === 'image/jpg') return '.jpg';

  const clean = uri.split('?')[0].toLowerCase();
  if (clean.endsWith('.png')) return '.png';
  if (clean.endsWith('.webp')) return '.webp';
  return '.jpg';
}

/**
 * Menyalin foto sementara hasil kamera/galeri ke direktori persisten aplikasi
 * agar tidak terhapus oleh pembersihan cache OS saat teknisi lama berada di area offline.
 */
export async function persistEvidencePhoto(
  sourceUri: string,
  clientOperationId: string,
  mimeType?: string | null,
): Promise<string> {
  try {
    if (!sourceUri || sourceUri.includes(`/${EVIDENCE_DIR_NAME}/`)) {
      return sourceUri;
    }
    const evidenceDir = new Directory(Paths.document, EVIDENCE_DIR_NAME);
    evidenceDir.create({ intermediates: true, idempotent: true });

    const safeId = clientOperationId.replace(/[^A-Za-z0-9_-]/g, '_');
    const ext = inferExtension(sourceUri, mimeType);
    const destFile = new File(evidenceDir, `${safeId}${ext}`);
    const srcFile = new File(sourceUri);
    await srcFile.copy(destFile);
    return destFile.uri;
  } catch {
    return sourceUri;
  }
}

/**
 * Menghapus salinan foto bukti internal setelah draft tersinkron atau dibuang manual.
 * Tidak pernah menghapus foto asli di galeri pengguna.
 */
export async function cleanupEvidencePhoto(uri: string | null | undefined): Promise<void> {
  if (!uri || !uri.includes(`/${EVIDENCE_DIR_NAME}/`)) {
    return;
  }
  try {
    const file = new File(uri);
    if (file.exists) {
      file.delete();
    }
  } catch {
    // Abaikan kegagalan hapus file lokal
  }
}
