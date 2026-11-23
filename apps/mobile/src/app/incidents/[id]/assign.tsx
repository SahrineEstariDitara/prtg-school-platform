import React from 'react';
import { useLocalSearchParams } from 'expo-router';
import { AssignTechnicianScreen } from '../../../features/assignments/AssignTechnicianScreen';
import { EmptyState } from '../../../shared/components';

export default function IncidentAssignRoute() {
  const { id, regionId } = useLocalSearchParams<{ id?: string; regionId?: string }>();
  const incidentId = Number(id);
  const parsedRegion = regionId ? Number(regionId) : undefined;
  if (!Number.isInteger(incidentId) || incidentId <= 0) {
    return <EmptyState title="ID incident tidak valid" />;
  }
  return (
    <AssignTechnicianScreen
      incidentId={incidentId}
      regionId={parsedRegion && Number.isInteger(parsedRegion) ? parsedRegion : undefined}
    />
  );
}
