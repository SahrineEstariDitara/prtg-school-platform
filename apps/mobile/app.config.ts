import fs from 'node:fs';
import path from 'node:path';
import type { ExpoConfig, ConfigContext } from 'expo/config';
import { withAndroidManifest, withAppBuildGradle, withProjectBuildGradle } from 'expo/config-plugins';

const WIN_CMAKE_SNIPPET = `
// PRTG_WIN_CMAKE_SHORT_PATH: Prevent Windows 260-char MAX_PATH failures in CMake/Ninja
if (org.gradle.internal.os.OperatingSystem.current().isWindows()) {
  def driveRoot = rootProject.rootDir.absolutePath.substring(0, 3)
  def rxStagingBase = new File(driveRoot, "c")
  subprojects { subproject ->
    def shortId = Integer.toString(Math.abs(subproject.name.hashCode()) % 1296, 36)
    subproject.plugins.withId("com.android.library") {
      def androidExt = subproject.extensions.findByName("android")
      if (androidExt != null) {
        androidExt.externalNativeBuild {
          cmake {
            if (buildStagingDirectory == null) {
              buildStagingDirectory = new File(rxStagingBase, shortId)
            }
          }
        }
      }
    }
  }
}
`;

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

  const withManifest = withAndroidManifest(baseConfig, (mod) => {
    const app = mod.modResults.manifest.application?.[0];
    if (app) {
      app.$['android:usesCleartextTraffic'] = allowCleartext ? 'true' : 'false';
    }
    return mod;
  });

  const withRootGradle = withProjectBuildGradle(withManifest, (mod) => {
    if (!mod.modResults.contents.includes('PRTG_WIN_CMAKE_SHORT_PATH')) {
      mod.modResults.contents = `${mod.modResults.contents.trimEnd()}\n${WIN_CMAKE_SNIPPET}`;
    }
    return mod;
  });

  return withAppBuildGradle(withRootGradle, (mod) => {
    if (!mod.modResults.contents.includes('PRTG_APP_CMAKE_SHORT_PATH')) {
      mod.modResults.contents = mod.modResults.contents.replace(
        /android\s*\{/,
        `android {\n    // PRTG_APP_CMAKE_SHORT_PATH\n    if (org.gradle.internal.os.OperatingSystem.current().isWindows()) {\n        externalNativeBuild {\n            cmake {\n                buildStagingDirectory = new File(rootProject.rootDir.absolutePath.substring(0, 3), "c/a")\n            }\n        }\n        defaultConfig {\n            externalNativeBuild {\n                cmake {\n                    arguments "-DCMAKE_OBJECT_PATH_MAX=230"\n                }\n            }\n        }\n    }`,
      );
    }
    return mod;
  });
};
