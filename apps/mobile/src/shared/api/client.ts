import type { ApiEnvelope, ApiErrorDetail } from '../types/api';
import type { AuthTokenResponse } from '../types/domain';
import { clearTokenPair, getTokenPair, saveTokenPair } from '../storage/secureTokens';

const DEFAULT_API_BASE_URL = 'http://10.0.2.2:5001/api/v1';

export class ApiClientError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: ApiErrorDetail[];
  readonly requestId?: string;
  readonly isNetworkError: boolean;

  constructor(params: {
    message: string;
    status: number;
    code: string;
    details?: ApiErrorDetail[];
    requestId?: string;
    isNetworkError?: boolean;
  }) {
    super(params.message);
    this.name = 'ApiClientError';
    this.status = params.status;
    this.code = params.code;
    this.details = params.details;
    this.requestId = params.requestId;
    this.isNetworkError = Boolean(params.isNetworkError);
  }
}

export function getApiBaseUrl(): string {
  const envUrl = process.env.EXPO_PUBLIC_API_URL;
  return (envUrl && envUrl.trim() ? envUrl.trim() : DEFAULT_API_BASE_URL).replace(/\/+$/, '');
}

export function getServerOrigin(): string {
  return getApiBaseUrl().replace(/\/api\/v1$/, '');
}

export function generateClientId(prefix = 'mob'): string {
  const randomPart =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}-${Math.random()
          .toString(36)
          .slice(2, 10)}`;
  return `${prefix}-${randomPart}`;
}

type UnauthorizedHandler = () => void;

let onUnauthorizedCallback: UnauthorizedHandler | null = null;
let refreshInFlight: Promise<string | null> | null = null;

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  onUnauthorizedCallback = handler;
}

async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (err) {
    const isAbort = err instanceof Error && err.name === 'AbortError';
    throw new ApiClientError({
      message: isAbort
        ? 'Koneksi ke server habis waktu (timeout). Periksa jaringan Anda.'
        : 'Tidak dapat terhubung ke server backend. Pastikan koneksi aktif.',
      status: 0,
      code: isAbort ? 'TIMEOUT' : 'NETWORK_ERROR',
      isNetworkError: true,
    });
  } finally {
    clearTimeout(timer);
  }
}

async function performSingleFlightRefresh(): Promise<string | null> {
  if (refreshInFlight) {
    return refreshInFlight;
  }

  refreshInFlight = (async () => {
    try {
      const { refreshToken } = await getTokenPair();
      if (!refreshToken) {
        return null;
      }

      const requestId = generateClientId('req');
      const res = await fetchWithTimeout(
        `${getApiBaseUrl()}/auth/refresh`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'application/json',
            'X-Request-ID': requestId,
          },
          body: JSON.stringify({ refresh_token: refreshToken }),
        },
        15000,
      );

      if (!res.ok) {
        await clearTokenPair();
        onUnauthorizedCallback?.();
        return null;
      }

      const envelope = (await res.json()) as ApiEnvelope<AuthTokenResponse>;
      if (!envelope.success || !envelope.data?.access_token || !envelope.data?.refresh_token) {
        await clearTokenPair();
        onUnauthorizedCallback?.();
        return null;
      }

      await saveTokenPair(envelope.data.access_token, envelope.data.refresh_token);
      return envelope.data.access_token;
    } catch {
      return null;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

export interface ApiRequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  formData?: FormData;
  query?: Record<string, string | number | boolean | null | undefined>;
  skipAuth?: boolean;
  timeoutMs?: number;
}

function buildUrl(
  path: string,
  query?: Record<string, string | number | boolean | null | undefined>,
): string {
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  const base = `${getApiBaseUrl()}${cleanPath}`;
  if (!query) return base;

  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && String(value).trim() !== '') {
      params.append(key, String(value));
    }
  }
  const qs = params.toString();
  return qs ? `${base}?${qs}` : base;
}

export async function apiRequest<T>(
  path: string,
  options: ApiRequestOptions = {},
  _retried = false,
): Promise<T> {
  const method = options.method ?? 'GET';
  const requestId = generateClientId('req');
  const headers: Record<string, string> = {
    Accept: 'application/json',
    'X-Request-ID': requestId,
  };

  if (!options.formData && options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }

  if (!options.skipAuth) {
    const { accessToken } = await getTokenPair();
    if (accessToken) {
      headers.Authorization = `Bearer ${accessToken}`;
    }
  }

  const url = buildUrl(path, options.query);
  const timeoutMs = options.timeoutMs ?? (options.formData ? 30000 : 15000);
  const response = await fetchWithTimeout(
    url,
    {
      method,
      headers,
      body: options.formData
        ? options.formData
        : options.body !== undefined
          ? JSON.stringify(options.body)
          : undefined,
    },
    timeoutMs,
  );

  // Single-flight refresh retry on 401
  if (response.status === 401 && !options.skipAuth && !_retried) {
    const refreshedToken = await performSingleFlightRefresh();
    if (refreshedToken) {
      return apiRequest<T>(path, options, true);
    }
  }

  let parsed: ApiEnvelope<T> | null = null;
  try {
    parsed = (await response.json()) as ApiEnvelope<T>;
  } catch {
    parsed = null;
  }

  if (!response.ok || !parsed || !parsed.success) {
    const errPayload = parsed && !parsed.success ? parsed.error : null;
    throw new ApiClientError({
      message: errPayload?.message || `Permintaan gagal (HTTP ${response.status}).`,
      status: response.status,
      code: errPayload?.code || `HTTP_${response.status}`,
      details: errPayload?.details,
      requestId: parsed?.meta?.request_id ?? requestId,
    });
  }

  return parsed.data;
}

export function buildAttachmentFullUrl(attachmentPath: string): string {
  if (attachmentPath.startsWith('http://') || attachmentPath.startsWith('https://')) {
    return attachmentPath;
  }
  const clean = attachmentPath.startsWith('/') ? attachmentPath : `/${attachmentPath}`;
  return `${getServerOrigin()}${clean}`;
}
