import * as SecureStore from 'expo-secure-store';
import { apiRequest, setUnauthorizedHandler } from '../src/shared/api/client';

describe('API client (Bearer auth, X-Request-ID, single-flight JWT refresh)', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    jest.clearAllMocks();
    setUnauthorizedHandler(null);
  });

  afterAll(() => {
    global.fetch = originalFetch;
  });

  test('Attaches Bearer token and X-Request-ID header on authenticated request', async () => {
    (SecureStore.getItemAsync as jest.Mock).mockImplementation(async (key: string) =>
      key.includes('access') ? 'token-abc' : 'refresh-xyz',
    );

    const mockFetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        success: true,
        data: { total_schools: 160 },
        meta: { request_id: 'req-1', timestamp: '2026-11-23T00:00:00Z' },
      }),
    });
    global.fetch = mockFetch as unknown as typeof fetch;

    const data = await apiRequest<{ total_schools: number }>('/dashboard');
    expect(data.total_schools).toBe(160);

    const [, init] = mockFetch.mock.calls[0];
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer token-abc');
    expect(headers['X-Request-ID']).toMatch(/^req-/);
  });

  test('Performs single-flight refresh on 401 and retries original request', async () => {
    let currentAccess = 'expired-access';
    let currentRefresh = 'valid-refresh';

    (SecureStore.getItemAsync as jest.Mock).mockImplementation(async (key: string) =>
      key.includes('access') ? currentAccess : currentRefresh,
    );
    (SecureStore.setItemAsync as jest.Mock).mockImplementation(async (key: string, val: string) => {
      if (key.includes('access')) currentAccess = val;
      if (key.includes('refresh')) currentRefresh = val;
    });

    const mockFetch = jest
      .fn()
      // 1st call -> 401
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        json: async () => ({
          success: false,
          error: { code: 'UNAUTHORIZED', message: 'Token expired' },
        }),
      })
      // 2nd call -> /auth/refresh succeeds
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          success: true,
          data: {
            access_token: 'fresh-access',
            refresh_token: 'rotated-refresh',
            token_type: 'Bearer',
            expires_in: 1800,
          },
          meta: { request_id: 'req-ref', timestamp: '2026-11-23T00:00:00Z' },
        }),
      })
      // 3rd call -> retried original request succeeds
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          success: true,
          data: { id: 7, username: 'teknisi1' },
          meta: { request_id: 'req-me', timestamp: '2026-11-23T00:00:00Z' },
        }),
      });
    global.fetch = mockFetch as unknown as typeof fetch;

    const me = await apiRequest<{ id: number; username: string }>('/me');
    expect(me.username).toBe('teknisi1');
    expect(currentAccess).toBe('fresh-access');
    expect(currentRefresh).toBe('rotated-refresh');
    expect(mockFetch).toHaveBeenCalledTimes(3);
  });

  test('Triggers unauthorized callback and clears tokens when refresh fails', async () => {
    (SecureStore.getItemAsync as jest.Mock).mockImplementation(async (key: string) =>
      key.includes('access') ? 'expired-access' : 'revoked-refresh',
    );
    const onUnauth = jest.fn();
    setUnauthorizedHandler(onUnauth);

    const mockFetch = jest
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        json: async () => ({
          success: false,
          error: { code: 'UNAUTHORIZED', message: 'Expired' },
        }),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        json: async () => ({
          success: false,
          error: { code: 'UNAUTHORIZED', message: 'Refresh revoked' },
        }),
      });
    global.fetch = mockFetch as unknown as typeof fetch;

    await expect(apiRequest('/me')).rejects.toThrow();
    expect(onUnauth).toHaveBeenCalledTimes(1);
    expect(SecureStore.deleteItemAsync).toHaveBeenCalled();
  });
});
