import * as SecureStore from 'expo-secure-store';

const ACCESS_TOKEN_KEY = 'prtg_school_access_token';
const REFRESH_TOKEN_KEY = 'prtg_school_refresh_token';
const CACHED_USER_KEY = 'prtg_school_cached_user';

export interface StoredTokenPair {
  accessToken: string | null;
  refreshToken: string | null;
}

export async function saveTokenPair(accessToken: string, refreshToken: string): Promise<void> {
  await Promise.all([
    SecureStore.setItemAsync(ACCESS_TOKEN_KEY, accessToken),
    SecureStore.setItemAsync(REFRESH_TOKEN_KEY, refreshToken),
  ]);
}

export async function getTokenPair(): Promise<StoredTokenPair> {
  const [accessToken, refreshToken] = await Promise.all([
    SecureStore.getItemAsync(ACCESS_TOKEN_KEY),
    SecureStore.getItemAsync(REFRESH_TOKEN_KEY),
  ]);
  return { accessToken, refreshToken };
}

export async function clearTokenPair(): Promise<void> {
  await Promise.all([
    SecureStore.deleteItemAsync(ACCESS_TOKEN_KEY),
    SecureStore.deleteItemAsync(REFRESH_TOKEN_KEY),
    SecureStore.deleteItemAsync(CACHED_USER_KEY),
  ]);
}

export async function saveCachedUserJson(userJson: string): Promise<void> {
  await SecureStore.setItemAsync(CACHED_USER_KEY, userJson);
}

export async function getCachedUserJson(): Promise<string | null> {
  return SecureStore.getItemAsync(CACHED_USER_KEY);
}
