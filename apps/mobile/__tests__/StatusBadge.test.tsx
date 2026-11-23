import React from 'react';
import { render } from '@testing-library/react-native';
import {
  FreshnessBanner,
  IncidentStatusBadge,
  NetworkStatusBadge,
  SyncStatusBadge,
} from '../src/shared/components/StatusBadge';

describe('StatusBadge & FreshnessBanner (FR-008 status separation)', () => {
  test('Renders separated text + icon badges for network, incident, and sync states', async () => {
    const { getByText } = await render(
      <>
        <NetworkStatusBadge status="offline" />
        <IncidentStatusBadge status="awaiting_recovery" />
        <SyncStatusBadge status="conflict" />
      </>,
    );

    expect(getByText('Jaringan: Offline')).toBeTruthy();
    expect(getByText('Incident: Menunggu Recheck PRTG')).toBeTruthy();
    expect(getByText('Sync: Konflik Server')).toBeTruthy();
  });

  test('FreshnessBanner explicitly warns when telemetry is stale or unknown (never assumes online)', async () => {
    const { getByText } = await render(
      <FreshnessBanner
        lastCheckedAt="2026-11-23T01:00:00Z"
        isStale={true}
        networkStatus="unknown"
      />,
    );

    expect(getByText('Data PRTG Stale / Tidak Pasti — Tidak Dianggap Online')).toBeTruthy();
  });
});
