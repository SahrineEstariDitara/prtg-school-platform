import React, { useEffect } from 'react';
import { useRouter } from 'expo-router';
import { useAuth } from '../features/auth/AuthContext';
import { LoadingState } from '../shared/components';

export default function IndexGate() {
  const router = useRouter();
  const { status, consumePendingDeepLink } = useAuth();

  useEffect(() => {
    if (status === 'authenticated') {
      const deepLink = consumePendingDeepLink();
      if (deepLink) {
        router.replace(deepLink as never);
      } else {
        router.replace('/(tabs)/dashboard');
      }
    } else if (status === 'unauthenticated') {
      router.replace('/login');
    }
  }, [status, router, consumePendingDeepLink]);

  return <LoadingState message="Memeriksa sesi autentikasi..." />;
}
