import React from 'react';
import { Alert, StyleSheet, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useAuth } from './AuthContext';
import { useSync } from '../offline-sync/SyncContext';
import { getApiBaseUrl } from '../../shared/api/client';
import { AppButton, AppText, Card, ScreenContainer } from '../../shared/components';
import { colors, spacing } from '../../shared/theme/tokens';

export function ProfileScreen() {
  const router = useRouter();
  const { user, capabilities, isOfflineSession, logout, refreshProfile } = useAuth();
  const { isOnline, pendingCount, conflictCount } = useSync();

  const handleLogout = () => {
    Alert.alert('Keluar Akun?', 'Sesi token Anda akan dicabut dari perangkat ini.', [
      { text: 'Batal', style: 'cancel' },
      {
        text: 'Keluar',
        style: 'destructive',
        onPress: async () => {
          await logout();
          router.replace('/login');
        },
      },
    ]);
  };

  return (
    <ScreenContainer onRefresh={refreshProfile}>
      <Card style={styles.card}>
        <AppText variant="h2">{user?.full_name ?? '-'}</AppText>
        <AppText variant="body" color={colors.textSecondary}>
          @{user?.username ?? '-'} • {capabilities.displayName}
        </AppText>
        <View style={styles.metaList}>
          <AppText variant="caption" color={colors.textSecondary}>
            Scope Wilayah:{' '}
            {capabilities.isGlobalScope
              ? 'Global (Semua Wilayah DIY)'
              : (user?.regions ?? []).map((r) => r.name).join(', ') || '-'}
          </AppText>
          <AppText variant="caption" color={colors.textSecondary}>
            Status Tugas: {user?.is_on_duty ? 'Sedang Bertugas (On Duty)' : 'Off Duty'}
          </AppText>
          <AppText variant="caption" color={colors.textSecondary}>
            Koneksi: {isOnline ? 'Online' : 'Offline'}
            {isOfflineSession ? ' (Sesi Cache)' : ''}
          </AppText>
          <AppText variant="caption" color={colors.textMuted}>
            Endpoint API: {getApiBaseUrl()}
          </AppText>
        </View>
      </Card>

      <Card style={styles.card}>
        <AppText variant="h3">Pintasan Operasional</AppText>
        <AppButton
          label="Inbox Notifikasi"
          variant="secondary"
          icon="notifications-outline"
          onPress={() => router.push('/notifications')}
        />
        {capabilities.canUseOfflineDrafts ? (
          <AppButton
            label={`Antrean Draft Offline (${pendingCount} pending, ${conflictCount} konflik)`}
            variant="secondary"
            icon="cloud-upload-outline"
            onPress={() => router.push('/offline-queue')}
          />
        ) : null}
      </Card>

      <AppButton
        label="Keluar (Logout)"
        variant="danger"
        icon="log-out-outline"
        onPress={handleLogout}
      />
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  card: {
    gap: spacing.sm,
  },
  metaList: {
    marginTop: spacing.xs,
    gap: 4,
  },
});
