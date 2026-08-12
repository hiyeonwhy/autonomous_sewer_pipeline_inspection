import type {
  ApiDefect,
  ApiPageResponse,
  DefectCaptureCreate,
  DefectReviewUpdate,
} from '../types/api';
import { apiRequest } from './client';
import { normalizeListResponse } from './sessions';

export async function getDefectsBySession(
  sessionId: number,
  signal?: AbortSignal,
): Promise<ApiDefect[]> {
  const response = await apiRequest<ApiDefect[] | ApiPageResponse<ApiDefect>>(
    `/sessions/${String(sessionId)}/defects`,
    { signal },
  );
  return normalizeListResponse(response);
}

export function updateDefectReview(
  defectId: number,
  request: DefectReviewUpdate,
  signal?: AbortSignal,
): Promise<ApiDefect> {
  return apiRequest(`/defects/${String(defectId)}`, {
    method: 'PATCH',
    body: JSON.stringify(request),
    signal,
  });
}

export function createCaptureDefect(
  sessionId: number,
  request: DefectCaptureCreate,
  signal?: AbortSignal,
): Promise<ApiDefect> {
  return apiRequest(`/sessions/${String(sessionId)}/captures`, {
    method: 'POST',
    body: JSON.stringify(request),
    signal,
  });
}
