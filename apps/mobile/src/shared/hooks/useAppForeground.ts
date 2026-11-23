import { useEffect, useRef } from 'react';
import { AppState } from 'react-native';

/**
 * Menjalankan callback saat aplikasi kembali ke foreground (background/inactive -> active).
 * Sesuai SAD §9.4 untuk refresh ringan & pemicu sync queue aman.
 */
export function useAppForeground(onForeground: () => void): void {
  const prevState = useRef<string>(AppState.currentState ?? 'active');
  const callbackRef = useRef(onForeground);
  callbackRef.current = onForeground;

  useEffect(() => {
    const sub = AppState.addEventListener('change', (nextState) => {
      if (prevState.current !== 'active' && nextState === 'active') {
        callbackRef.current();
      }
      prevState.current = nextState ?? 'active';
    });
    return () => sub.remove();
  }, []);
}
