import type {
  ApiDriveLog,
  ApiPageResponse,
  ApiSession,
  CreateSessionRequest,
  DriveLogCreate,
  UpdateSessionRequest,
} from '../types/api';
import { apiRequest } from './client';

export function normalizeListResponse<T>(
  response: T[] | ApiPageResponse<T>,
): T[] {
  if (Array.isArray(response)) return response;
  return response.content ?? response.items ?? response.results ?? [];
}

export async function getSessions(signal?: AbortSignal): Promise<ApiSession[]> {
  const response = await apiRequest<ApiSession[] | ApiPageResponse<ApiSession>>(
    '/sessions',
    { signal },
  );
  return normalizeListResponse(response);
}

export function getSession(
  sessionId: number,
  signal?: AbortSignal,
): Promise<ApiSession> {
  return apiRequest(`/sessions/${String(sessionId)}`, { signal });
}

export function createSession(
  request: CreateSessionRequest,
  signal?: AbortSignal,
): Promise<ApiSession> {
  return apiRequest('/sessions', {
    method: 'POST',
    body: JSON.stringify(request),
    signal,
  });
}

export function updateSession(
  sessionId: number,
  request: UpdateSessionRequest,
  signal?: AbortSignal,
): Promise<ApiSession> {
  return apiRequest(`/sessions/${String(sessionId)}`, {
    method: 'PATCH',
    body: JSON.stringify(request),
    signal,
  });
}

export async function getSessionLogs(
  sessionId: number,
  signal?: AbortSignal,
): Promise<ApiDriveLog[]> {
  const response = await apiRequest<ApiDriveLog[] | ApiPageResponse<ApiDriveLog>>(
    `/sessions/${String(sessionId)}/drive-logs`,
    { signal },
  );
  return normalizeListResponse(response);
}

export function createDriveLog(
  sessionId: number,
  request: DriveLogCreate,
  signal?: AbortSignal,
): Promise<ApiDriveLog> {
  return apiRequest(`/sessions/${String(sessionId)}/drive-logs`, {
    method: 'POST',
    body: JSON.stringify(request),
    signal,
  });
}
