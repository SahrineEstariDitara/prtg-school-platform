import React from 'react';
import { useLocalSearchParams } from 'expo-router';
import { IncidentDetailScreen } from '../../../features/incidents/IncidentDetailScreen';
import { EmptyState } from '../../../shared/components';

export default function IncidentDetailRoute() {
  const { id } = useLocalSearchParams<{ id?: string }>();
  const incidentId = Number(id);
  if (!Number.isInteger(incidentId) || incidentId <= 0) {
    return <EmptyState title="ID incident tidak valid" />;
  }
  return <IncidentDetailScreen incidentId={incidentId} />;
}
