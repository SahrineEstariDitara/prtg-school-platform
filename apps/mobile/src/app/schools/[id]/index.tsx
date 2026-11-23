import React from 'react';
import { useLocalSearchParams } from 'expo-router';
import { SchoolDetailScreen } from '../../../features/schools/SchoolDetailScreen';
import { EmptyState } from '../../../shared/components';

export default function SchoolDetailRoute() {
  const { id } = useLocalSearchParams<{ id?: string }>();
  const schoolId = Number(id);
  if (!Number.isInteger(schoolId) || schoolId <= 0) {
    return <EmptyState title="ID sekolah tidak valid" />;
  }
  return <SchoolDetailScreen schoolId={schoolId} />;
}
