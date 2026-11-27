import React, { useState } from 'react';
import { Alert, Modal, StyleSheet, View } from 'react-native';
import { incidentsApi } from '../../shared/api/endpoints';
import { ApiClientError } from '../../shared/api/client';
import { validateMandatoryReason } from '../../shared/validation/validators';
import { AppButton, AppInput, AppText, Card } from '../../shared/components';
import { colors, radius, spacing } from '../../shared/theme/tokens';
import type { IncidentDetail } from '../../shared/types/domain';

export interface VerificationPanelProps {
  incident: IncidentDetail;
  isOnline: boolean;
  onUpdated: () => Promise<void>;
}

/**
 * Panel verifikasi khusus Supervisor Wilayah & Supervisor Utama (FR-017..020).
 * Komponen ini TIDAK PERNAH dirender untuk role Admin (ADR-011).
 */
export function VerificationPanel({ incident, isOnline, onUpdated }: VerificationPanelProps) {
  const [approveNotes, setApproveNotes] = useState('');
  const [rejectModalOpen, setRejectModalOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [rejectError, setRejectError] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<'approve' | 'reject' | 'recheck' | null>(null);

  const policy = incident.recovery_policy;
  const requiredN = policy?.required_consecutive_normal ?? 3;
  const currentStreak = incident.recovery_stable_streak ?? 0;

  const handleApprove = async () => {
    if (!isOnline) {
      Alert.alert('Wajib Online', 'Persetujuan verifikasi dan recheck PRTG memerlukan koneksi aktif.');
      return;
    }
    setBusyAction('approve');
    try {
      const res = await incidentsApi.approveVerification(
        incident.id,
        approveNotes.trim() || undefined,
      );
      const nextStatus = String(res.incident_status ?? 'awaiting_recovery');
      const recheckInfo =
        res.recheck && typeof res.recheck === 'object'
          ? (res.recheck as { error_message?: string | null; is_valid_response?: boolean })
          : null;
      const recheckErr =
        typeof recheckInfo?.error_message === 'string' && recheckInfo.error_message.trim()
          ? recheckInfo.error_message.trim()
          : null;

      const recheckMsg =
        typeof res.message === 'string'
          ? res.message
          : nextStatus === 'resolved'
            ? 'Semua sensor wajib terverifikasi normal dan incident ditutup (resolved).'
            : recheckErr
              ? `Verifikasi disetujui, status kini '${nextStatus}'. Recheck ke-1 belum valid: ${recheckErr}`
              : `Verifikasi disetujui. Status kini '${nextStatus}' — menunggu konfirmasi stabil PRTG (${requiredN}x normal).`;
      Alert.alert('Hasil Verifikasi & PRTG Recheck', recheckMsg);
      setApproveNotes('');
      await onUpdated();
    } catch (err) {
      Alert.alert(
        'Gagal Approve',
        err instanceof ApiClientError ? err.message : 'Gagal memproses verifikasi.',
      );
    } finally {
      setBusyAction(null);
    }
  };

  const handleReject = async () => {
    if (!isOnline) {
      Alert.alert('Wajib Online', 'Penolakan verifikasi memerlukan koneksi aktif.');
      return;
    }
    const check = validateMandatoryReason(rejectReason, 'Alasan penolakan');
    if (!check.valid) {
      setRejectError(check.message ?? 'Alasan penolakan wajib diisi.');
      return;
    }
    setRejectError(null);
    setBusyAction('reject');
    try {
      await incidentsApi.rejectVerification(incident.id, rejectReason.trim());
      setRejectModalOpen(false);
      setRejectReason('');
      Alert.alert(
        'Verifikasi Ditolak',
        'Incident dikembalikan ke status in_progress agar teknisi melanjutkan perbaikan.',
      );
      await onUpdated();
    } catch (err) {
      setRejectError(err instanceof ApiClientError ? err.message : 'Gagal menolak verifikasi.');
    } finally {
      setBusyAction(null);
    }
  };

  const handleManualRecheck = async () => {
    if (!isOnline) {
      Alert.alert('Wajib Online', 'Recheck PRTG live memerlukan koneksi aktif.');
      return;
    }
    setBusyAction('recheck');
    try {
      await incidentsApi.manualRecheck(incident.id);
      await onUpdated();
    } catch (err) {
      Alert.alert(
        'Recheck Gagal',
        err instanceof ApiClientError ? err.message : 'Gagal menghubungi server untuk recheck PRTG.',
      );
    } finally {
      setBusyAction(null);
    }
  };

  if (incident.incident_status === 'pending_verification') {
    return (
      <Card style={styles.verifyCard}>
        <AppText variant="h3" color={colors.purpleText}>
          Review Verifikasi Supervisor
        </AppText>
        <AppText variant="caption" color={colors.textSecondary}>
          Menyetujui laporan akan langsung memicu PRTG live recheck pada sensor wajib sekolah
          (tidak otomatis resolved sebelum memenuhi policy stabil).
        </AppText>

        {!policy.configured ? (
          <View style={styles.warnBox}>
            <AppText variant="captionBold" color={colors.warningText}>
              Perhatian: Recovery policy sekolah ini belum dikonfigurasi oleh Admin. Penutupan
              incident akan diblokir hingga sensor wajib ditetapkan.
            </AppText>
          </View>
        ) : (
          <AppText variant="caption" color={colors.textSecondary}>
            Policy aktif v{policy.version}: {policy.required_sensor_count} sensor wajib •{' '}
            {requiredN}x pembacaan normal berturut-turut.
          </AppText>
        )}

        <AppInput
          label="Catatan Persetujuan (Opsional)"
          placeholder="Mis. Bukti foto dan tindakan sudah sesuai..."
          value={approveNotes}
          onChangeText={setApproveNotes}
        />

        <View style={styles.actionRow}>
          <AppButton
            label="Approve & Recheck PRTG"
            variant="success"
            icon="checkmark-circle"
            loading={busyAction === 'approve'}
            disabled={!isOnline || busyAction !== null}
            onPress={handleApprove}
            style={styles.flexOne}
          />
          <AppButton
            label="Reject"
            variant="danger"
            icon="close-circle"
            disabled={!isOnline || busyAction !== null}
            onPress={() => setRejectModalOpen(true)}
          />
        </View>

        <Modal
          visible={rejectModalOpen}
          transparent
          animationType="slide"
          onRequestClose={() => setRejectModalOpen(false)}
        >
          <View style={styles.modalOverlay}>
            <Card style={styles.modalCard}>
              <AppText variant="h3" color={colors.danger}>
                Tolak Laporan Penyelesaian
              </AppText>
              <AppText variant="caption" color={colors.textSecondary}>
                Alasan penolakan wajib diisi. Incident akan kembali ke status in_progress untuk
                ditindaklanjuti teknisi.
              </AppText>
              <AppInput
                label="Alasan Penolakan *"
                placeholder="Jelaskan apa yang perlu diperbaiki teknisi..."
                multiline
                value={rejectReason}
                onChangeText={setRejectReason}
                errorText={rejectError}
              />
              <View style={styles.actionRow}>
                <AppButton
                  label="Batal"
                  variant="secondary"
                  onPress={() => {
                    setRejectModalOpen(false);
                    setRejectError(null);
                  }}
                  style={styles.flexOne}
                />
                <AppButton
                  label="Kirim Penolakan"
                  variant="danger"
                  loading={busyAction === 'reject'}
                  onPress={handleReject}
                  style={styles.flexOne}
                />
              </View>
            </Card>
          </View>
        </Modal>
      </Card>
    );
  }

  if (incident.incident_status === 'awaiting_recovery') {
    const ratio = Math.min(currentStreak / Math.max(requiredN, 1), 1);
    return (
      <Card style={styles.recoveryCard}>
        <AppText variant="h3" color={colors.warningText}>
          Menunggu Pemulihan Stabil PRTG (awaiting_recovery)
        </AppText>
        <AppText variant="bodyBold" color={colors.textPrimary}>
          Progres Recheck Normal: {currentStreak} / {requiredN} berturut-turut
        </AppText>
        <View style={styles.progressTrack}>
          <View style={[styles.progressFill, { width: `${Math.round(ratio * 100)}%` }]} />
        </View>
        <AppText variant="caption" color={colors.textSecondary}>
          Incident otomatis ditutup (resolved) hanya jika seluruh sensor wajib normal {requiredN}x
          berturut-turut. Jika gagal/timeout/offline, streak direset ke 0.
        </AppText>
        <AppButton
          label="Jalankan Recheck PRTG Sekarang"
          variant="primary"
          icon="pulse"
          loading={busyAction === 'recheck'}
          disabled={!isOnline || busyAction !== null}
          onPress={handleManualRecheck}
        />
      </Card>
    );
  }

  return null;
}

const styles = StyleSheet.create({
  verifyCard: {
    gap: spacing.sm,
    borderColor: colors.purpleBorder,
    backgroundColor: '#FAF5FF',
  },
  recoveryCard: {
    gap: spacing.sm,
    borderColor: colors.warningBorder,
    backgroundColor: '#FFFBEB',
  },
  warnBox: {
    padding: spacing.sm,
    borderRadius: radius.sm,
    backgroundColor: colors.warningBg,
    borderWidth: 1,
    borderColor: colors.warningBorder,
  },
  actionRow: {
    flexDirection: 'row',
    gap: spacing.sm,
    marginTop: spacing.xs,
  },
  flexOne: {
    flex: 1,
  },
  progressTrack: {
    height: 10,
    backgroundColor: colors.unknownBg,
    borderRadius: radius.pill,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    backgroundColor: colors.success,
    borderRadius: radius.pill,
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(15, 23, 42, 0.55)',
    justifyContent: 'center',
    padding: spacing.lg,
  },
  modalCard: {
    gap: spacing.md,
  },
});
