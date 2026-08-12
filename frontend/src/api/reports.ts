import type { ApiPageResponse, ApiReport } from '../types/api';
import { apiRequest } from './client';
import { normalizeListResponse } from './sessions';

export async function getReports(signal?: AbortSignal): Promise<ApiReport[]> {
  const response = await apiRequest<ApiReport[] | ApiPageResponse<ApiReport>>(
    '/reports',
    { signal },
  );
  return normalizeListResponse(response);
}

export function createReport(
  sessionId: number,
  signal?: AbortSignal,
): Promise<ApiReport> {
  return apiRequest(`/sessions/${String(sessionId)}/reports`, {
    method: 'POST',
    signal,
  });
}
