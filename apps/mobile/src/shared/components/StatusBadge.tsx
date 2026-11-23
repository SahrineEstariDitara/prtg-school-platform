import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { colors, radius, spacing, typography } from '../theme/tokens';
import type { IncidentStatus, NetworkStatus, SyncStatus } from '../types/domain';
import { formatDateTime } from '../hooks/formatters';

interface BadgeSpec {
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  bg: string;
  text: string;
  border: string;
}

const NETWORK_BADGE_MAP: Record<NetworkStatus, BadgeSpec> = {
  online: {
    label: 'Jaringan: Online',
    icon: 'checkmark-circle',
    bg: colors.onlineBg,
    text: colors.onlineText,
    border: colors.onlineBorder,
  },
  offline: {
    label: 'Jaringan: Offline',
    icon: 'close-circle',
    bg: colors.offlineBg,
    text: colors.offlineText,
    border: colors.offlineBorder,
  },
  warning: {
    label: 'Jaringan: Warning',
    icon: 'warning',
    bg: colors.warningBg,
    text: colors.warningText,
    border: colors.warningBorder,
  },
  unknown: {
    label: 'Jaringan: Unknown',
    icon: 'help-circle',
    bg: colors.unknownBg,
    text: colors.unknownText,
    border: colors.unknownBorder,
  },
};

const INCIDENT_BADGE_MAP: Record<IncidentStatus, BadgeSpec> = {
  detected: {
    label: 'Incident: Terdeteksi',
    icon: 'radio',
    bg: colors.offlineBg,
    text: colors.offlineText,
    border: colors.offlineBorder,
  },
  unassigned: {
    label: 'Incident: Belum Ditugaskan',
    icon: 'person-add',
    bg: colors.warningBg,
    text: colors.warningText,
    border: colors.warningBorder,
  },
  assigned: {
    label: 'Incident: Ditugaskan',
    icon: 'person',
    bg: colors.infoBg,
    text: colors.infoText,
    border: colors.infoBorder,
  },
  acknowledged: {
    label: 'Incident: Diterima Teknisi',
    icon: 'hand-left',
    bg: colors.infoBg,
    text: colors.infoText,
    border: colors.infoBorder,
  },
  in_progress: {
    label: 'Incident: Sedang Ditangani',
    icon: 'construct',
    bg: colors.primarySoft,
    text: colors.primaryDark,
    border: colors.infoBorder,
  },
  pending_verification: {
    label: 'Incident: Menunggu Verifikasi',
    icon: 'shield-checkmark',
    bg: colors.purpleBg,
    text: colors.purpleText,
    border: colors.purpleBorder,
  },
  awaiting_recovery: {
    label: 'Incident: Menunggu Recheck PRTG',
    icon: 'pulse',
    bg: colors.warningBg,
    text: colors.warningText,
    border: colors.warningBorder,
  },
  resolved: {
    label: 'Incident: Selesai (Resolved)',
    icon: 'checkmark-done-circle',
    bg: colors.onlineBg,
    text: colors.onlineText,
    border: colors.onlineBorder,
  },
};

const SYNC_BADGE_MAP: Record<SyncStatus, BadgeSpec> = {
  draft: {
    label: 'Sync: Draft Lokal',
    icon: 'document-outline',
    bg: colors.unknownBg,
    text: colors.unknownText,
    border: colors.unknownBorder,
  },
  pending_sync: {
    label: 'Sync: Menunggu Sinkronisasi',
    icon: 'cloud-upload-outline',
    bg: colors.infoBg,
    text: colors.infoText,
    border: colors.infoBorder,
  },
  synced: {
    label: 'Sync: Tersinkron',
    icon: 'cloud-done',
    bg: colors.onlineBg,
    text: colors.onlineText,
    border: colors.onlineBorder,
  },
  conflict: {
    label: 'Sync: Konflik Server',
    icon: 'git-compare',
    bg: colors.offlineBg,
    text: colors.offlineText,
    border: colors.offlineBorder,
  },
  failed: {
    label: 'Sync: Gagal Kirim',
    icon: 'alert-circle',
    bg: colors.warningBg,
    text: colors.warningText,
    border: colors.warningBorder,
  },
};

function BaseBadge({ spec, testID }: { spec: BadgeSpec; testID?: string }) {
  return (
    <View
      testID={testID}
      style={[
        styles.badge,
        { backgroundColor: spec.bg, borderColor: spec.border },
      ]}
    >
      <Ionicons name={spec.icon} size={13} color={spec.text} style={styles.icon} />
      <Text style={[typography.badge, { color: spec.text }]}>{spec.label}</Text>
    </View>
  );
}

export const NetworkStatusBadge = React.memo(function NetworkStatusBadge({
  status,
  testID,
}: {
  status: NetworkStatus;
  testID?: string;
}) {
  return <BaseBadge spec={NETWORK_BADGE_MAP[status] ?? NETWORK_BADGE_MAP.unknown} testID={testID} />;
});

export const IncidentStatusBadge = React.memo(function IncidentStatusBadge({
  status,
  testID,
}: {
  status: IncidentStatus;
  testID?: string;
}) {
  return <BaseBadge spec={INCIDENT_BADGE_MAP[status] ?? INCIDENT_BADGE_MAP.detected} testID={testID} />;
});

export const SyncStatusBadge = React.memo(function SyncStatusBadge({
  status,
  testID,
}: {
  status: SyncStatus;
  testID?: string;
}) {
  return <BaseBadge spec={SYNC_BADGE_MAP[status] ?? SYNC_BADGE_MAP.draft} testID={testID} />;
});

export const FreshnessBanner = React.memo(function FreshnessBanner({
  lastCheckedAt,
  isStale,
  networkStatus,
}: {
  lastCheckedAt: string | null;
  isStale: boolean;
  networkStatus?: NetworkStatus;
}) {
  const showWarning = isStale || networkStatus === 'unknown';
  return (
    <View
      testID="freshness-banner"
      style={[
        styles.banner,
        showWarning ? styles.bannerWarning : styles.bannerNeutral,
      ]}
    >
      <Ionicons
        name={showWarning ? 'time-outline' : 'checkmark-circle-outline'}
        size={16}
        color={showWarning ? colors.warningText : colors.textSecondary}
        style={styles.bannerIcon}
      />
      <View style={styles.bannerTextWrap}>
        <Text
          style={[
            typography.captionBold,
            { color: showWarning ? colors.warningText : colors.textSecondary },
          ]}
        >
          {showWarning
            ? 'Data PRTG Stale / Tidak Pasti — Tidak Dianggap Online'
            : 'Data PRTG Terkini'}
        </Text>
        <Text style={[typography.caption, { color: colors.textSecondary }]}>
          Terakhir dicek PRTG: {formatDateTime(lastCheckedAt)}
        </Text>
      </View>
    </View>
  );
});

export const OfflineBanner = React.memo(function OfflineBanner({
  isOnline,
  pendingCount,
}: {
  isOnline: boolean;
  pendingCount: number;
}) {
  if (isOnline && pendingCount === 0) {
    return null;
  }

  return (
    <View
      testID="offline-banner"
      style={[styles.banner, !isOnline ? styles.bannerOffline : styles.bannerInfo]}
    >
      <Ionicons
        name={!isOnline ? 'cloud-offline' : 'sync'}
        size={16}
        color={!isOnline ? colors.offlineText : colors.infoText}
        style={styles.bannerIcon}
      />
      <View style={styles.bannerTextWrap}>
        <Text
          style={[
            typography.captionBold,
            { color: !isOnline ? colors.offlineText : colors.infoText },
          ]}
        >
          {!isOnline
            ? 'Mode Offline Terbatas — Menampilkan Cache Lokal'
            : `${pendingCount} Draft Menunggu Sinkronisasi`}
        </Text>
        <Text style={[typography.caption, { color: colors.textSecondary }]}>
          {!isOnline
            ? 'Anda dapat membaca cache & menulis draft tindakan. Acknowledge, submit, assign, dan verifikasi memerlukan koneksi.'
            : 'Draft akan dikirim otomatis secara aman ke server.'}
        </Text>
      </View>
    </View>
  );
});

const styles = StyleSheet.create({
  badge: {
    flexDirection: 'row',
    alignItems: 'center',
    alignSelf: 'flex-start',
    paddingHorizontal: spacing.sm,
    paddingVertical: 4,
    borderRadius: radius.pill,
    borderWidth: 1,
  },
  icon: {
    marginRight: 4,
  },
  banner: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    padding: spacing.md,
    borderRadius: radius.md,
    borderWidth: 1,
  },
  bannerWarning: {
    backgroundColor: colors.warningBg,
    borderColor: colors.warningBorder,
  },
  bannerNeutral: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
  },
  bannerOffline: {
    backgroundColor: colors.offlineBg,
    borderColor: colors.offlineBorder,
  },
  bannerInfo: {
    backgroundColor: colors.infoBg,
    borderColor: colors.infoBorder,
  },
  bannerIcon: {
    marginRight: spacing.sm,
    marginTop: 2,
  },
  bannerTextWrap: {
    flex: 1,
    gap: 2,
  },
});
