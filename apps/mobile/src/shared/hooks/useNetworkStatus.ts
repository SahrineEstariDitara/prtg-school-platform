import { useEffect, useState } from 'react';
import NetInfo, { type NetInfoState } from '@react-native-community/netinfo';

export interface NetworkConnectionState {
  isConnected: boolean;
  isInternetReachable: boolean;
  connectionType: string;
}

function parseNetState(state: NetInfoState): NetworkConnectionState {
  const connected = Boolean(state.isConnected);
  const reachable =
    state.isInternetReachable === null ? connected : Boolean(state.isInternetReachable);
  return {
    isConnected: connected,
    isInternetReachable: connected && reachable,
    connectionType: state.type,
  };
}

export function useNetworkStatus(): NetworkConnectionState {
  const [netState, setNetState] = useState<NetworkConnectionState>({
    isConnected: true,
    isInternetReachable: true,
    connectionType: 'unknown',
  });

  useEffect(() => {
    let mounted = true;
    NetInfo.fetch().then((initial) => {
      if (mounted) {
        setNetState(parseNetState(initial));
      }
    });
    const unsub = NetInfo.addEventListener((next) => {
      if (mounted) {
        setNetState(parseNetState(next));
      }
    });
    return () => {
      mounted = false;
      unsub();
    };
  }, []);

  return netState;
}
