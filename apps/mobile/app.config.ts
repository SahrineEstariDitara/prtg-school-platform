import fs from 'node:fs';
import path from 'node:path';
import type { ExpoConfig, ConfigContext } from 'expo/config';
import { withAndroidManifest } from 'expo/config-plugins';

export default ({ config }: ConfigContext): ExpoConfig => {
  const googleServicesPath = path.resolve(__dirname, 'google-services.json');
  const hasGoogleServices = fs.existsSync(googleServicesPath);
  const apiBaseUrl = (
    process.env.EXPO_PUBLIC_API_URL || 'http://10.0.2.2:5001/api/v1'
  ).trim();
  const allowCleartext =
    process.env.EXPO_PUBLIC_ALLOW_CLEARTEXT === 'true' ||
    apiBaseUrl.toLowerCase().startsWith('http://');

  const baseConfig: ExpoConfig = {
    ...config,
    name: 'PRTG School Mobile',
    slug: 'prtg-school-mobile',
    version: '1.0.0',
    scheme: 'prtgschool',
    orientation: 'portrait',
    userInterfaceStyle: 'light',
    icon: './assets/icon.png',
    ios: {
      supportsTablet: true,
      bundleIdentifier: 'id.jogjaprov.prtgschool',
    },
    android: {
      package: 'id.jogjaprov.prtgschool',
      blockedPermissions: ['android.permission.RECORD_AUDIO'],
      adaptiveIcon: {
        foregroundImage: './assets/android-icon-foreground.png',
        backgroundColor: '#1D4ED8',
      },
      ...(hasGoogleServices ? { googleServicesFile: './google-services.json' } : {}),
    },
    plugins: [
      ['expo-router', { root: './src/app' }],
      'expo-secure-store',
      'expo-sqlite',
      'expo-image',
      [
        'expo-image-picker',
        {
          photosPermission: 'Izinkan akses galeri untuk melampirkan foto bukti penanganan incident.',
          cameraPermission: 'Izinkan akses kamera untuk mengambil foto bukti penanganan di lapangan.',
          microphonePermission: false,
        },
      ],
      [
        'expo-notifications',
        {
          color: '#1D4ED8',
          defaultChannel: 'prtg-incidents',
        },
      ],
    ],
    experiments: {
      typedRoutes: true,
    },
    extra: {
      apiBaseUrl,
      allowCleartext,
      hasGoogleServices,
    },
  };

  return withAndroidManifest(baseConfig, (mod) => {
    const app = mod.modResults.manifest.application?.[0];
    if (app) {
      app.$['android:usesCleartextTraffic'] = allowCleartext ? 'true' : 'false';
    }
    return mod;
  });
};
