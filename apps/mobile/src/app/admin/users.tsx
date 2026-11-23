import React from 'react';
import { UsersAdminScreen } from '../../features/administration/UsersAdminScreen';
import { useAuth } from '../../features/auth/AuthContext';
import { EmptyState } from '../../shared/components';

export default function AdminUsersRoute() {
  const { capabilities } = useAuth();
  if (!capabilities.canManageAdmin) {
    return <EmptyState icon="lock-closed-outline" title="Khusus Role Admin" />;
  }
  return <UsersAdminScreen />;
}
