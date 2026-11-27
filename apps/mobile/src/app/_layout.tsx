import React from 'react';
import { StyleSheet, View } from 'react-native';
import { Stack } from 'expo-router';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import { AuthProvider } from '../features/auth/AuthContext';
import { SyncProvider } from '../features/offline-sync/SyncContext';
import { NotificationProvider } from '../features/notifications/NotificationProvider';
import { colors } from '../shared/theme/tokens';

export default function RootLayout() {
  return (
    <View style={styles.root}>
      <SafeAreaProvider>
        <AuthProvider>
          <SyncProvider>
            <NotificationProvider>
              <StatusBar style="dark" />
              <Stack
                screenOptions={{
                  headerStyle: { backgroundColor: colors.surface },
                  headerTintColor: colors.textPrimary,
                  headerTitleStyle: { fontWeight: '700' },
                  contentStyle: { backgroundColor: colors.bg },
                }}
              >
                <Stack.Screen name="index" options={{ headerShown: false }} />
                <Stack.Screen name="login" options={{ headerShown: false }} />
                <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
                <Stack.Screen name="schools/[id]/index" options={{ title: 'Detail Sekolah' }} />
                <Stack.Screen
                  name="schools/[id]/policy"
                  options={{ title: 'Recovery Policy Sensor' }}
                />
                <Stack.Screen name="incidents/[id]/index" options={{ title: 'Detail Incident' }} />
                <Stack.Screen
                  name="incidents/[id]/respond"
                  options={{ title: 'Catat Tindakan Teknisi' }}
                />
                <Stack.Screen
                  name="incidents/[id]/assign"
                  options={{ title: 'Penugasan Teknisi' }}
                />
                <Stack.Screen name="admin/users" options={{ title: 'Manajemen Pengguna' }} />
                <Stack.Screen name="admin/regions" options={{ title: 'Manajemen Wilayah' }} />
                <Stack.Screen name="admin/audit-logs" options={{ title: 'Audit Log Sistem' }} />
                <Stack.Screen name="notifications" options={{ title: 'Inbox Notifikasi' }} />
                <Stack.Screen name="offline-queue" options={{ title: 'Antrean Draft Offline' }} />
              </Stack>
            </NotificationProvider>
          </SyncProvider>
        </AuthProvider>
      </SafeAreaProvider>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
  },
});
