import React, { useEffect } from 'react';
import { Tabs, useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '../../features/auth/AuthContext';
import { useSync } from '../../features/offline-sync/SyncContext';
import { colors } from '../../shared/theme/tokens';

export default function RoleTabsLayout() {
  const router = useRouter();
  const { status, capabilities } = useAuth();
  const { pendingCount, conflictCount } = useSync();

  useEffect(() => {
    if (status === 'unauthenticated') {
      router.replace('/login');
    }
  }, [status, router]);

  const showTab = (tabName: string) => capabilities.visibleTabs.includes(tabName as never);
  const queueBadge =
    capabilities.canUseOfflineDrafts && pendingCount + conflictCount > 0
      ? pendingCount + conflictCount
      : undefined;

  const iconColor = (c: unknown) => (typeof c === 'string' ? c : colors.textMuted);

  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.textMuted,
        tabBarStyle: {
          backgroundColor: colors.surface,
          borderTopColor: colors.border,
        },
        headerStyle: { backgroundColor: colors.surface },
        headerTintColor: colors.textPrimary,
        headerTitleStyle: { fontWeight: '700' },
      }}
    >
      <Tabs.Screen
        name="dashboard"
        options={{
          title: 'Dashboard',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="speedometer-outline" size={size} color={iconColor(color)} />
          ),
        }}
      />
      <Tabs.Screen
        name="schools"
        options={{
          title: 'Sekolah',
          href: showTab('schools') ? '/(tabs)/schools' : null,
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="school-outline" size={size} color={iconColor(color)} />
          ),
        }}
      />
      <Tabs.Screen
        name="incidents"
        options={{
          title: capabilities.roleCode === 'teknisi' ? 'Tugas Saya' : 'Incident',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="alert-circle-outline" size={size} color={iconColor(color)} />
          ),
        }}
      />
      <Tabs.Screen
        name="queue"
        options={{
          title: capabilities.queueTabTitle,
          href: showTab('queue') ? '/(tabs)/queue' : null,
          tabBarBadge: queueBadge,
          tabBarIcon: ({ color, size }) => (
            <Ionicons
              name={
                capabilities.canUseOfflineDrafts
                  ? 'cloud-upload-outline'
                  : capabilities.canVerifyIncident
                    ? 'shield-checkmark-outline'
                    : 'people-outline'
              }
              size={size}
              color={iconColor(color)}
            />
          ),
        }}
      />
      <Tabs.Screen
        name="admin"
        options={{
          title: 'Admin',
          href: showTab('admin') ? '/(tabs)/admin' : null,
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="settings-outline" size={size} color={iconColor(color)} />
          ),
        }}
      />
      <Tabs.Screen
        name="profile"
        options={{
          title: 'Profil',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="person-circle-outline" size={size} color={iconColor(color)} />
          ),
        }}
      />
    </Tabs>
  );
}
