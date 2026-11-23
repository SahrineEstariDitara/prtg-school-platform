import React from 'react';
import { StyleSheet } from 'react-native';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { View } from 'react-native';
import { AppText, Card, ScreenContainer } from '../../shared/components';
import { colors, spacing } from '../../shared/theme/tokens';

interface AdminTile {
  title: string;
  subtitle: string;
  icon: keyof typeof Ionicons.glyphMap;
  route: string;
}

const ADMIN_TILES: AdminTile[] = [
  {
    title: 'Manajemen Pengguna & Role',
    subtitle: 'Buat/edit akun, aktif/nonaktif, atur 5 role tetap & scope wilayah (ADR-012).',
    icon: 'people-outline',
    route: '/admin/users',
  },
  {
    title: 'Manajemen Wilayah DIY',
    subtitle: 'Lihat, tambah, atau perbarui wilayah monitoring.',
    icon: 'map-outline',
    route: '/admin/regions',
  },
  {
    title: 'Recovery Policy Sensor Wajib',
    subtitle: 'Buka daftar sekolah lalu pilih sekolah untuk mengatur sensor wajib pemulihan.',
    icon: 'shield-outline',
    route: '/(tabs)/schools',
  },
  {
    title: 'Audit Log Administratif',
    subtitle: 'Tinjau seluruh catatan perubahan user, wilayah, sekolah, dan konfigurasi.',
    icon: 'document-lock-outline',
    route: '/admin/audit-logs',
  },
];

export function AdminMenuScreen() {
  const router = useRouter();

  return (
    <ScreenContainer>
      <Card style={styles.infoCard}>
        <AppText variant="h3" color={colors.primaryDark}>
          Panel Administrasi Sistem
        </AppText>
        <AppText variant="caption" color={colors.textSecondary}>
          Sesuai ADR-011, Admin mengelola akun, wilayah, recovery policy, dan audit log dengan
          akses incident read-only (verifikasi dilakukan oleh Supervisor Wilayah / Supervisor Utama).
        </AppText>
      </Card>

      {ADMIN_TILES.map((tile) => (
        <Card
          key={tile.title}
          onPress={() => router.push(tile.route as never)}
          style={styles.tileCard}
        >
          <View style={styles.tileRow}>
            <Ionicons name={tile.icon} size={24} color={colors.primary} />
            <View style={styles.tileBody}>
              <AppText variant="bodyBold">{tile.title}</AppText>
              <AppText variant="caption" color={colors.textSecondary}>
                {tile.subtitle}
              </AppText>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.textMuted} />
          </View>
        </Card>
      ))}
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  infoCard: {
    gap: 4,
    backgroundColor: colors.primarySoft,
    borderColor: colors.infoBorder,
  },
  tileCard: {
    padding: spacing.md,
  },
  tileRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
  },
  tileBody: {
    flex: 1,
    gap: 2,
  },
});
