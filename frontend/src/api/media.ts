import { apiRequest, API_ORIGIN } from './client';

export interface CaptureUploadResponse {
  snapshot_path: string;
}

export const PLACEHOLDER_IMAGE =
  'data:image/svg+xml;charset=UTF-8,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540">' +
      '<rect width="960" height="540" fill="#111827"/>' +
      '<path d="M360 355l88-105 58 68 45-52 91 89H360z" fill="#475569"/>' +
      '<circle cx="580" cy="205" r="34" fill="#64748b"/>' +
      '<text x="480" y="420" text-anchor="middle" fill="#cbd5e1" font-family="sans-serif" font-size="28">이미지 없음</text>' +
    '</svg>',
  );

export function uploadCapture(
  image: Blob,
  signal?: AbortSignal,
): Promise<CaptureUploadResponse> {
  const formData = new FormData();
  formData.append('file', image, 'capture.png');

  return apiRequest('/media/captures', {
    method: 'POST',
    body: formData,
    signal,
  });
}

export function resolveMediaUrl(path: string): string {
  if (/^(https?:|data:)/i.test(path)) return path;
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `${API_ORIGIN}${normalizedPath}`;
}
