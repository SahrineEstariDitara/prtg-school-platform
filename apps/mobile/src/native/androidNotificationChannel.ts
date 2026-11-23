import { Platform } from 'react-native';
import * as Notifications from 'expo-notifications';

export const PRTG_INCIDENT_CHANNEL_ID = 'prtg-incidents';

/**
 * Konfigurasi Android Notification Channel prioritas tinggi untuk incident PRTG (SAD §9.4, M6.7).
 */
export async function ensureAndroidNotificationChannel(): Promise<void> {
  if (Platform.OS !== 'android') {
    return;
  }
  await Notifications.setNotificationChannelAsync(PRTG_INCIDENT_CHANNEL_ID, {
    name: 'Gangguan & Incident Sekolah',
    description: 'Notifikasi gangguan jaringan sekolah, penugasan teknisi, dan hasil verifikasi.',
    importance: Notifications.AndroidImportance.HIGH,
    vibrationPattern: [0, 250, 200, 250],
    lightColor: '#DC2626',
    sound: 'default',
  });
}
