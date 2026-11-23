import React, { useCallback, useEffect, useState } from 'react';
import { Alert, StyleSheet, View } from 'react-native';
import { adminApi } from '../../shared/api/endpoints';
import { ApiClientError } from '../../shared/api/client';
import {
  AppButton,
  AppInput,
  AppText,
  Card,
  ErrorState,
  LoadingState,
  ScreenContainer,
} from '../../shared/components';
import { colors, spacing } from '../../shared/theme/tokens';
import type { RegionRef } from '../../shared/types/domain';

export function RegionsAdminScreen() {
  const [regions, setRegions] = useState<RegionRef[]>([]);
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const loadRegions = useCallback(async () => {
    setLoading(true);
    setErrorMsg(null);
    try {
      const res = await adminApi.listRegions(true);
      setRegions(res.items);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Gagal memuat daftar wilayah.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRegions();
  }, [loadRegions]);

  const handleCreate = async () => {
    if (!code.trim() || !name.trim()) {
      Alert.alert('Validasi', 'Kode dan nama wilayah wajib diisi.');
      return;
    }
    setSaving(true);
    try {
      await adminApi.createRegion({ code: code.trim(), name: name.trim() });
      setCode('');
      setName('');
      await loadRegions();
    } catch (err) {
      Alert.alert('Gagal', err instanceof ApiClientError ? err.message : 'Gagal menambah wilayah.');
    } finally {
      setSaving(false);
    }
  };

  if (loading && regions.length === 0) {
    return <LoadingState message="Memuat wilayah..." />;
  }
  if (errorMsg && regions.length === 0) {
    return <ErrorState message={errorMsg} onRetry={loadRegions} />;
  }

  return (
    <ScreenContainer onRefresh={loadRegions}>
      <Card style={styles.formCard}>
        <AppText variant="h3">Tambah Wilayah Baru</AppText>
        <AppInput
          label="Kode Wilayah"
          placeholder="Mis. diy_timur"
          autoCapitalize="none"
          value={code}
          onChangeText={setCode}
        />
        <AppInput
          label="Nama Wilayah"
          placeholder="Mis. Wilayah Timur"
          value={name}
          onChangeText={setName}
        />
        <AppButton label="Simpan Wilayah" icon="add-circle-outline" loading={saving} onPress={handleCreate} />
      </Card>

      <AppText variant="h3">Daftar Wilayah ({regions.length})</AppText>
      {regions.map((r) => (
        <Card key={r.id} style={styles.itemCard}>
          <View style={styles.rowBetween}>
            <View style={styles.flexOne}>
              <AppText variant="bodyBold">{r.name}</AppText>
              <AppText variant="caption" color={colors.textMuted}>
                Kode: {r.code}
              </AppText>
            </View>
            <AppText
              variant="captionBold"
              color={r.is_active !== false ? colors.onlineText : colors.offlineText}
            >
              {r.is_active !== false ? 'AKTIF' : 'NONAKTIF'}
            </AppText>
          </View>
        </Card>
      ))}
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  formCard: {
    gap: spacing.sm,
  },
  itemCard: {
    padding: spacing.md,
  },
  rowBetween: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.sm,
  },
  flexOne: {
    flex: 1,
  },
});
