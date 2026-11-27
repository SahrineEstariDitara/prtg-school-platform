import React, { useState } from 'react';
import { Alert, StyleSheet, View } from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import { useRouter } from 'expo-router';
import { incidentsApi } from '../../shared/api/endpoints';
import { ApiClientError, generateClientId } from '../../shared/api/client';
import { useSync } from '../offline-sync/SyncContext';
import {
  validateAttachmentFile,
  validateResponseSummary,
} from '../../shared/validation/validators';
import {
  AppButton,
  AppInput,
  AppText,
  Card,
  OfflineBanner,
  PhotoPreview,
  ScreenContainer,
} from '../../shared/components';
import { colors, spacing } from '../../shared/theme/tokens';

interface PickedPhoto {
  uri: string;
  name: string;
  mimeType: string;
}

export function ResponseFormScreen({
  incidentId,
  schoolName,
}: {
  incidentId: number;
  schoolName: string;
}) {
  const router = useRouter();
  const { isOnline, pendingCount, saveDraft } = useSync();

  const [actionSummary, setActionSummary] = useState('');
  const [resultNotes, setResultNotes] = useState('');
  const [photo, setPhoto] = useState<PickedPhoto | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const pickImage = async (fromCamera: boolean) => {
    try {
      const perm = fromCamera
        ? await ImagePicker.requestCameraPermissionsAsync()
        : await ImagePicker.requestMediaLibraryPermissionsAsync();

      if (!perm.granted) {
        Alert.alert(
          'Izin Ditolak',
          'Aplikasi memerlukan izin kamera/galeri untuk melampirkan bukti.',
        );
        return;
      }

      const result = fromCamera
        ? await ImagePicker.launchCameraAsync({
            mediaTypes: ['images'],
            quality: 0.75,
          })
        : await ImagePicker.launchImageLibraryAsync({
            mediaTypes: ['images'],
            quality: 0.75,
          });

      if (result.canceled || !result.assets?.[0]) {
        return;
      }

      const asset = result.assets[0];
      const mime = asset.mimeType || 'image/jpeg';
      const check = validateAttachmentFile({ mimeType: mime, fileSize: asset.fileSize ?? null });
      if (!check.valid) {
        Alert.alert('Foto Tidak Valid', check.message ?? 'Periksa format & ukuran foto.');
        return;
      }

      setPhoto({
        uri: asset.uri,
        name: asset.fileName || `bukti-${incidentId}-${Date.now()}.jpg`,
        mimeType: mime,
      });
    } catch (err) {
      Alert.alert(
        'Gagal Membuka Media',
        err instanceof Error
          ? err.message
          : 'Kamera/galeri tidak tersedia di perangkat atau emulator ini.',
      );
    }
  };

  const handleSaveOffline = async () => {
    await saveDraft({
      incidentId,
      schoolName,
      actionSummary: actionSummary.trim(),
      resultNotes: resultNotes.trim() || null,
      attachmentUri: photo?.uri ?? null,
      attachmentMime: photo?.mimeType ?? null,
      attachmentName: photo?.name ?? null,
    });
    Alert.alert(
      'Tersimpan di Draft Offline',
      'Catatan tindakan tersimpan aman di perangkat dan akan disinkronkan otomatis saat koneksi pulih.',
    );
    router.back();
  };

  const handleSubmit = async () => {
    const check = validateResponseSummary(actionSummary);
    if (!check.valid) {
      setSummaryError(check.message ?? 'Ringkasan tindakan wajib diisi.');
      return;
    }
    setSummaryError(null);
    setSubmitting(true);

    try {
      if (!isOnline) {
        await handleSaveOffline();
        return;
      }

      const clientOpId = generateClientId('op');
      try {
        await incidentsApi.createResponse(incidentId, {
          action_summary: actionSummary.trim(),
          result_notes: resultNotes.trim() || null,
          client_operation_id: clientOpId,
          attachment: photo
            ? { uri: photo.uri, name: photo.name, type: photo.mimeType }
            : null,
        });
        Alert.alert('Tersimpan', 'Catatan tindakan berhasil dikirim ke server.');
        router.back();
      } catch (err) {
        if (err instanceof ApiClientError && err.isNetworkError) {
          // Fallback aman ke draft SQLite bila koneksi terputus saat kirim
          await handleSaveOffline();
          return;
        }
        Alert.alert(
          'Gagal Mengirim',
          err instanceof ApiClientError ? err.message : 'Gagal menyimpan tindakan.',
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <ScreenContainer>
      <OfflineBanner isOnline={isOnline} pendingCount={pendingCount} />

      <Card style={styles.card}>
        <AppText variant="h3">
          Incident #{incidentId} — {schoolName}
        </AppText>
        <AppText variant="caption" color={colors.textSecondary}>
          Isi ringkasan tindakan lapangan. Bila sedang offline, form otomatis tersimpan sebagai
          draft lokal yang aman disinkronkan ulang (idempoten).
        </AppText>

        <AppInput
          label="Ringkasan Tindakan *"
          placeholder="Mis. Restart ONT & cek kabel fiber di ruang server"
          value={actionSummary}
          onChangeText={setActionSummary}
          errorText={summaryError}
        />

        <AppInput
          label="Catatan Hasil / Temuan (Opsional)"
          placeholder="Mis. Lampu LOS padam, ping gateway normal kembali"
          multiline
          value={resultNotes}
          onChangeText={setResultNotes}
        />

        <View style={styles.photoSection}>
          <AppText variant="captionBold" color={colors.textSecondary}>
            Foto Bukti Lapangan (Opsional, JPG/PNG/WebP maks 5 MB)
          </AppText>
          <View style={styles.row}>
            <AppButton
              label="Kamera"
              variant="secondary"
              icon="camera-outline"
              onPress={() => pickImage(true)}
              style={styles.flexOne}
            />
            <AppButton
              label="Galeri"
              variant="secondary"
              icon="image-outline"
              onPress={() => pickImage(false)}
              style={styles.flexOne}
            />
          </View>
          {photo ? (
            <View style={styles.photoBox}>
              <PhotoPreview uri={photo.uri} isLocalFile height={180} />
              <AppButton
                label="Hapus Foto"
                variant="ghost"
                icon="trash-outline"
                onPress={() => setPhoto(null)}
              />
            </View>
          ) : null}
        </View>

        <AppButton
          label={isOnline ? 'Kirim Catatan Tindakan' : 'Simpan Draft Offline'}
          icon={isOnline ? 'cloud-upload-outline' : 'save-outline'}
          loading={submitting}
          onPress={handleSubmit}
        />
      </Card>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  card: {
    gap: spacing.md,
  },
  photoSection: {
    gap: spacing.sm,
  },
  row: {
    flexDirection: 'row',
    gap: spacing.sm,
  },
  flexOne: {
    flex: 1,
  },
  photoBox: {
    gap: spacing.xs,
  },
});
