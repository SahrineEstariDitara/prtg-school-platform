import React from 'react';
import { useLocalSearchParams } from 'expo-router';
import { ResponseFormScreen } from '../../../features/responses/ResponseFormScreen';
import { EmptyState } from '../../../shared/components';

export default function IncidentRespondRoute() {
  const { id, schoolName } = useLocalSearchParams<{ id?: string; schoolName?: string }>();
  const incidentId = Number(id);
  if (!Number.isInteger(incidentId) || incidentId <= 0) {
    return <EmptyState title="ID incident tidak valid" />;
  }
  return (
    <ResponseFormScreen
      incidentId={incidentId}
      schoolName={schoolName || `Sekolah #${incidentId}`}
    />
  );
}
