import React from 'react';
import { useLocalSearchParams } from 'expo-router';
import { RecoveryPolicyScreen } from '../../../features/administration/RecoveryPolicyScreen';
import { useAuth } from '../../../features/auth/AuthContext';
import { EmptyState } from '../../../shared/components';

export default function SchoolPolicyRoute() {
  const { id } = useLocalSearchParams<{ id?: string }>();
  const { capabilities } = useAuth();
  const schoolId = Number(id);

  if (!capabilities.canManageAdmin) {
    return <EmptyState icon="lock-closed-outline" title="Khusus Role Admin" />;
  }
  if (!Number.isInteger(schoolId) || schoolId <= 0) {
    return <EmptyState title="ID sekolah tidak valid" />;
  }
  return <RecoveryPolicyScreen schoolId={schoolId} />;
}
