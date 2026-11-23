import React from 'react';
import { AuditLogsScreen } from '../../features/administration/AuditLogsScreen';
import { useAuth } from '../../features/auth/AuthContext';
import { EmptyState } from '../../shared/components';

export default function AdminAuditLogsRoute() {
  const { capabilities } = useAuth();
  if (!capabilities.canManageAdmin) {
    return <EmptyState icon="lock-closed-outline" title="Khusus Role Admin" />;
  }
  return <AuditLogsScreen />;
}
