import React from 'react';
import { AdminMenuScreen } from '../../features/administration/AdminMenuScreen';
import { useAuth } from '../../features/auth/AuthContext';
import { EmptyState } from '../../shared/components';

export default function AdminTabRoute() {
  const { capabilities } = useAuth();
  if (!capabilities.canManageAdmin) {
    return (
      <EmptyState
        icon="lock-closed-outline"
        title="Akses Terbatas"
        subtitle="Halaman administrasi hanya tersedia untuk role Admin."
      />
    );
  }
  return <AdminMenuScreen />;
}
