import React, { useCallback, useEffect, useState } from 'react';
import { Alert, StyleSheet, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { incidentsApi } from '../../shared/api/endpoints';
import { ApiClientError } from '../../shared/api/client';
import { validateMandatoryReason } from '../../shared/validation/validators';
import {
  AppButton,
  AppInput,
  AppText,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../shared/components';
import { colors, spacing } from '../../shared/theme/tokens';
import type { TechnicianWorkloadItem } from '../../shared/types/domain';

interface TechRowProps {
  tech: TechnicianWorkloadItem;
  selected: boolean;
  onSelect: (id: number) => void;
}

const TechnicianRow = React.memo(function TechnicianRow({
  tech,
  selected,
  onSelect,
}: TechRowProps) {
  const handlePress = useCallback(() => onSelect(tech.id), [onSelect, tech.id]);

  return (
    <Card
      onPress={handlePress}
      style={selected ? styles.techCardSelected : styles.techCard}
    >
      <View style={styles.rowBetween}>
        <AppText variant="bodyBold" style={styles.flexOne}>
          {tech.full_name} ({tech.username})
        </AppText>
        <AppText
          variant="captionBold"
          color={tech.is_on_duty ? colors.onlineText : colors.textMuted}
        >
          {tech.is_on_duty ? 'ON DUTY' : 'OFF DUTY'}
        </AppText>
      </View>
      <AppText variant="caption" color={colors.textSecondary}>
        Beban Aktif: {tech.active_workload} incident • Wilayah:{' '}
        {tech.regions.map((r) => r.name).join(', ')}
      </AppText>
    </Card>
  );
});

export function AssignTechnicianScreen({
  incidentId,
  regionId,
}: {
  incidentId: number;
  regionId?: number;
}) {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [techs, setTechs] = useState<TechnicianWorkloadItem[]>([]);
  const [selectedTechId, setSelectedTechId] = useState<number | null>(null);
  const [reason, setReason] = useState('');
  const [reasonError, setReasonError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const loadTechnicians = useCallback(async () => {
    setLoading(true);
    setErrorMsg(null);
    try {
      const res = await incidentsApi.listTechnicians(regionId);
      setTechs(res.items);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Gagal memuat daftar teknisi.');
    } finally {
      setLoading(false);
    }
  }, [regionId]);

  useEffect(() => {
    loadTechnicians();
  }, [loadTechnicians]);

  const handleAssign = async () => {
    if (!selectedTechId) {
      Alert.alert('Pilih Teknisi', 'Pilih salah satu teknisi terlebih dahulu.');
      return;
    }
    const check = validateMandatoryReason(reason, 'Alasan penugasan');
    if (!check.valid) {
      setReasonError(check.message ?? 'Alasan wajib diisi.');
      return;
    }
    setReasonError(null);
    setSubmitting(true);
    try {
      await incidentsApi.assign(incidentId, selectedTechId, reason.trim());
      Alert.alert('Berhasil', 'Teknisi berhasil ditugaskan dan histori assignment tersimpan.');
      router.back();
    } catch (err) {
      Alert.alert(
        'Gagal Assign',
        err instanceof ApiClientError ? err.message : 'Gagal menugaskan teknisi.',
      );
    } finally {
      setSubmitting(false);
    }
  };

  const renderItem = useCallback(
    ({ item }: { item: TechnicianWorkloadItem }) => (
      <TechnicianRow
        tech={item}
        selected={selectedTechId === item.id}
        onSelect={setSelectedTechId}
      />
    ),
    [selectedTechId],
  );

  const keyExtractor = useCallback((item: TechnicianWorkloadItem) => String(item.id), []);

  if (loading) {
    return <LoadingState message="Memuat daftar teknisi wilayah..." />;
  }
  if (errorMsg) {
    return <ErrorState message={errorMsg} onRetry={loadTechnicians} />;
  }

  return (
    <View style={styles.root}>
      <View style={styles.headerBox}>
        <AppText variant="h3">Pilih Teknisi untuk Incident #{incidentId}</AppText>
        <AppText variant="caption" color={colors.textSecondary}>
          Urutan menampilkan teknisi aktif beserta jumlah incident aktif saat ini.
        </AppText>
        <AppInput
          label="Alasan Penugasan / Reassignment *"
          placeholder="Mis. Teknisi terdekat dari lokasi sekolah..."
          value={reason}
          onChangeText={setReason}
          errorText={reasonError}
        />
      </View>

      <FlashList
        data={techs}
        renderItem={renderItem}
        keyExtractor={keyExtractor}
        contentContainerStyle={styles.listContent}
        ListEmptyComponent={
          <EmptyState
            icon="people-outline"
            title="Tidak ada teknisi tersedia di wilayah ini"
          />
        }
      />

      <View style={[styles.footerBar, { paddingBottom: Math.max(insets.bottom, spacing.lg) }]}>
        <AppButton
          label="Simpan Penugasan"
          icon="checkmark-circle"
          loading={submitting}
          disabled={!selectedTechId}
          onPress={handleAssign}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  headerBox: {
    padding: spacing.lg,
    gap: spacing.sm,
    backgroundColor: colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  listContent: {
    padding: spacing.lg,
    paddingBottom: spacing.xxl,
  },
  techCard: {
    marginBottom: spacing.sm,
    padding: spacing.md,
    gap: 4,
  },
  techCardSelected: {
    marginBottom: spacing.sm,
    padding: spacing.md,
    gap: 4,
    borderColor: colors.primary,
    borderWidth: 2,
    backgroundColor: colors.primarySoft,
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
  footerBar: {
    padding: spacing.lg,
    backgroundColor: colors.surface,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
});
