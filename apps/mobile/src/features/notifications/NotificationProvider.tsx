import React, { useEffect, useRef } from 'react';
import { Platform } from 'react-native';
import * as Notifications from 'expo-notifications';
import { useRouter } from 'expo-router';
import { useAuth } from '../auth/AuthContext';
import { notificationsApi } from '../../shared/api/endpoints';
import { ensureAndroidNotificationChannel } from '../../native/androidNotificationChannel';
import { extractIncidentRouteFromNotificationData } from '../../navigation/guards';

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: true,
  }),
});

/**
 * Mengelola registrasi push token FCM/Expo dan deep-link notifikasi untuk 3 lifecycle:
 * Foreground, Background, dan Terminated (SAD §9.4, TC-13).
 */
export function NotificationProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { status, user, setPendingDeepLink } = useAuth();
  const registeredForUserRef = useRef<number | null>(null);
  const authStatusRef = useRef(status);
  authStatusRef.current = status;

  // 1. Setup channel Android, cek cold-start (1x mount), dan daftarkan listener tap notifikasi
  useEffect(() => {
    ensureAndroidNotificationChannel().catch(() => undefined);

    Notifications.getLastNotificationResponseAsync()
      .then((lastResponse) => {
        const data = lastResponse?.notification.request.content.data as
          | Record<string, unknown>
          | undefined;
        const route = extractIncidentRouteFromNotificationData(data);
        if (route) {
          setPendingDeepLink(route);
        }
      })
      .catch(() => undefined);

    const responseSub = Notifications.addNotificationResponseReceivedListener((response) => {
      const data = response.notification.request.content.data as
        | Record<string, unknown>
        | undefined;
      const route = extractIncidentRouteFromNotificationData(data);
      if (!route) return;

      if (authStatusRef.current === 'authenticated') {
        router.push(route as never);
      } else {
        setPendingDeepLink(route);
      }
    });

    return () => {
      responseSub.remove();
    };
  }, [router, setPendingDeepLink]);

  // 2. Registrasi token perangkat ke backend setelah login berhasil (POST /api/v1/devices/register)
  useEffect(() => {
    if (status !== 'authenticated' || !user) {
      registeredForUserRef.current = null;
      return;
    }
    if (registeredForUserRef.current === user.id) {
      return;
    }

    let cancelled = false;
    (async () => {
      try {
        const perms = await Notifications.getPermissionsAsync();
        let finalStatus = perms.status;
        if (finalStatus !== 'granted') {
          const req = await Notifications.requestPermissionsAsync();
          finalStatus = req.status;
        }
        if (finalStatus !== 'granted' || cancelled) {
          return;
        }

        // Ambil native FCM/APNs device token bila tersedia (Dev Build dengan google-services.json)
        let pushToken: string | null = null;
        try {
          const devicePush = await Notifications.getDevicePushTokenAsync();
          if (typeof devicePush?.data === 'string' && devicePush.data.length >= 8) {
            pushToken = devicePush.data;
          }
        } catch {
          // Di Expo Go atau sebelum google-services.json dipasang, fallback aman tanpa crash
          pushToken = null;
        }

        if (pushToken && !cancelled) {
          const platform = Platform.OS === 'ios' ? 'ios' : 'android';
          await notificationsApi.registerDevice(platform, pushToken);
          registeredForUserRef.current = user.id;
        }
      } catch {
        // Tidak memblokir login jika registrasi push gagal saat offline/dev
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [status, user]);

  return <>{children}</>;
}
