import React from 'react';
import { RegionsAdminScreen } from '../../features/administration/RegionsAdminScreen';
import { useAuth } from '../../features/auth/AuthContext';
import { EmptyState } from '../../shared/components';

export default function AdminRegionsRoute() {
  const { capabilities } = useAuth();
  if (!capabilities.canManageAdmin) {
    return <EmptyState icon="lock-closed-outline" title="Khusus Role Admin" />;
  }
  return <RegionsAdminScreen />;
}
