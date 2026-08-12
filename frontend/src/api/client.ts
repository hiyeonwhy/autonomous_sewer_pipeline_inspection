// 기본값이 '/api' 였습니다. vite 는 '/api' 를 프록시하지 않으므로 env 가 없으면
// 요청이 개발 서버 자신에게 가고 index.html 이 돌아와 JSON 파싱 에러가 납니다.
// 프록시가 실제로 가로채는 접두사를 기본값으로 둡니다.
const configuredBaseUrl =
  import.meta.env.VITE_API_BASE_URL ?? '/backend-api';
const configuredOrigin =
  import.meta.env.VITE_API_ORIGIN ?? 'https://i15c102.p.ssafy.io';

export const API_BASE_URL = configuredBaseUrl.replace(/\/+$/, '');
export const API_ORIGIN = configuredOrigin.replace(/\/+$/, '');
export const USE_MOCK =
  String(import.meta.env.VITE_USE_MOCK ?? 'false').toLowerCase() === 'true';

export class ApiError extends Error {
  readonly status: number;
  readonly body?: unknown;

  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

function getErrorMessage(body: unknown, fallback: string): string {
  if (typeof body === 'string' && body.trim()) return body;
  if (!body || typeof body !== 'object' || !('detail' in body)) return fallback;

  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) =>
        item && typeof item === 'object' && 'msg' in item
          ? String((item as { msg: unknown }).msg)
          : String(item),
      )
      .join(', ');
  }
  return fallback;
}

export async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }

  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  const response = await fetch(`${API_BASE_URL}${normalizedPath}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const contentType = response.headers.get('content-type') ?? '';
    let body: unknown;
    try {
      body = contentType.includes('application/json')
        ? await response.json()
        : await response.text();
    } catch {
      body = undefined;
    }

    throw new ApiError(
      response.status,
      getErrorMessage(body, `API 요청 실패: ${response.status}`),
      body,
    );
  }

  if (response.status === 204) return undefined as T;

  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.includes('text/html')) {
    // 200 인데 HTML 이면 API 가 아니라 개발 서버의 index.html 입니다 (프록시가
    // 경로를 안 가로챈 경우). 문자열이 데이터로 캐스팅돼 렌더에서 터지는 것보다
    // 여기서 원인을 말하고 실패하는 편이 낫습니다.
    throw new ApiError(
      response.status,
      'API 응답이 HTML 입니다 — VITE_API_BASE_URL 또는 vite 프록시 설정을 확인하세요.',
    );
  }
  // geo+json 등 JSON 계열은 전부 파싱합니다.
  if (!contentType.includes('json')) {
    return (await response.text()) as T;
  }
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(
      response.status,
      'API 응답 JSON 파싱 실패 — 응답이 잘렸거나 형식이 깨졌습니다.',
    );
  }
}
