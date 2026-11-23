import React, { useCallback, useEffect, useState } from 'react';
import { Alert, StyleSheet, View } from 'react-native';
import { useRouter } from 'expo-router';
import { adminApi, monitoringApi } from '../../shared/api/endpoints';
import { ApiClientError } from '../../shared/api/client';
import {
  AppButton,
  AppInput,
  AppText,
  Card,
  ChipFilter,
  ErrorState,
  LoadingState,
  NetworkStatusBadge,
  ScreenContainer,
} from '../../shared/components';
import { colors, spacing } from '../../shared/theme/tokens';
import type { PrtgSensorItem } from '../../shared/types/domain';

export function RecoveryPolicyScreen({ schoolId }: { schoolId: number }) {
  const router = useRouter();
  const [schoolName, setSchoolName] = useState('');
  const [sensors, setSensors] = useState<PrtgSensorItem[]>([]);
  const [selectedSensorIds, setSelectedSensorIds] = useState<number[]>([]);
  const [consecutiveNormal, setConsecutiveNormal] = useState('3');
  const [intervalSec, setIntervalSec] = useState('60');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setErrorMsg(null);
    try {
      const [snRes, polRes] = await Promise.all([
        monitoringApi.schoolSensors(schoolId),
        adminApi.getRecoveryPolicy(schoolId),
      ]);
      setSchoolName(snRes.school_name);
      setSensors(snRes.sensors);
      setSelectedSensorIds(polRes.required_sensors.map((s) => s.sensor_id));
      setConsecutiveNormal(String(polRes.required_consecutive_normal || 3));
      setIntervalSec(String(polRes.recheck_interval_seconds || 60));
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Gagal memuat recovery policy sekolah.');
    } finally {
      setLoading(false);
    }
  }, [schoolId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const toggleSensor = (sensorId: number) => {
    setSelectedSensorIds((prev) =>
      prev.includes(sensorId) ? prev.filter((id) => id !== sensorId) : [...prev, sensorId],
    );
  };

  const handleSave = async () => {
    if (selectedSensorIds.length === 0) {
      Alert.alert('Validasi', 'Pilih minimal 1 sensor wajib pemulihan.');
      return;
    }
    const nVal = Math.max(Number(consecutiveNormal) || 3, 1);
    const intervalVal = Math.max(Number(intervalSec) || 60, 10);

    setSaving(true);
    try {
      await adminApi.upsertRecoveryPolicy(schoolId, {
        required_sensor_ids: selectedSensorIds,
        required_consecutive_normal: nVal,
        recheck_interval_seconds: intervalVal,
        expected_status: 'online',
      });
      Alert.alert('Tersimpan', 'Recovery policy sensor wajib berhasil diperbarui.');
      router.back();
    } catch (err) {
      Alert.alert('Gagal', err instanceof ApiClientError ? err.message : 'Gagal menyimpan policy.');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <LoadingState message="Memuat sensor & recovery policy..." />;
  }
  if (errorMsg) {
    return <ErrorState message={errorMsg} onRetry={loadData} />;
  }

  return (
    <ScreenContainer>
      <Card style={styles.card}>
        <AppText variant="h2">{schoolName}</AppText>
        <AppText variant="caption" color={colors.textSecondary}>
          Tentukan sensor PRTG wajib yang harus normal berturut-turut saat PRTG Recheck sebelum
          incident boleh ditutup menjadi resolved (FR-019, ADR-009).
        </AppText>

        <AppInput
          label="Jumlah Pembacaan Normal Berturut-turut (N)"
          keyboardType="numeric"
          value={consecutiveNormal}
          onChangeText={setConsecutiveNormal}
        />
        <AppInput
          label="Interval Recheck (detik)"
          keyboardType="numeric"
          value={intervalSec}
          onChangeText={setIntervalSec}
        />
      </Card>

      <AppText variant="h3">
        Pilih Sensor Wajib ({selectedSensorIds.length} / {sensors.length} dipilih)
      </AppText>

      {sensors.map((sn) => {
        const selected = selectedSensorIds.includes(sn.id);
        return (
          <Card
            key={sn.id}
            onPress={() => toggleSensor(sn.id)}
            style={[styles.sensorCard, selected ? styles.selectedCard : null]}
          >
            <View style={styles.rowBetween}>
              <AppText variant="bodyBold" style={styles.flexOne}>
                {sn.sensor_name}
              </AppText>
              <ChipFilter
                label={selected ? 'Wajib' : 'Pilih'}
                selected={selected}
                onPress={() => toggleSensor(sn.id)}
              />
            </View>
            <AppText variant="caption" color={colors.textMuted}>
              Device: {sn.device_name ?? '-'} • PRTG ID: {sn.prtg_sensor_id}
            </AppText>
            <NetworkStatusBadge status={sn.normalized_status} />
          </Card>
        );
      })}

      <AppButton
        label="Simpan Recovery Policy"
        icon="save-outline"
        loading={saving}
        onPress={handleSave}
      />
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  card: {
    gap: spacing.sm,
  },
  sensorCard: {
    padding: spacing.md,
    gap: spacing.xs,
  },
  selectedCard: {
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
});
