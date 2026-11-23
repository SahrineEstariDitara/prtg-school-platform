import fs from 'node:fs';
import path from 'node:path';
import type { ExpoConfig, ConfigContext } from 'expo/config';

export default ({ config }: ConfigContext): ExpoConfig => {
  const googleServicesPath = path.resolve(__dirname, 'google-services.json');
  const hasGoogleServices = fs.existsSync(googleServicesPath);

  return {
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
      apiBaseUrl: process.env.EXPO_PUBLIC_API_URL || 'http://10.0.2.2:5001/api/v1',
      hasGoogleServices,
    },
  };
};
