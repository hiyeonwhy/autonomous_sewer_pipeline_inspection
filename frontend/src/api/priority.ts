import { apiRequest } from './client';

// 우선순위 산정 API (/api/priority/*)
// 응답 스키마: backend/cloud_api/app/domain/priority/schema.py

export interface PriorityGradeBand {
  label: string;
  share: number;
  description: string;
}

export interface PriorityMeta {
  model_version: string;
  generated_at: string;
  batch_id: string;
  input_digest: string;
  district_count: number;
  admin_basis: string;
  occurrence_weights: Record<string, number>;
  impact: { features: string[]; floor: number; span: number };
  /** 강수 트리거. applies_to_score=false 면 순위·등급에 반영되지 않습니다. */
  rain_trigger: { span: number; applies_to_score: boolean; field: string };
  display_names: Record<string, string>;
  grades: Record<string, PriorityGradeBand>;
  coverage: Record<string, number>;
  sources: Record<string, number>;
}

export interface PriorityScoreSummary {
  district_id: number;
  sgg_code: string;
  full_name: string;
  sido_code: string | null;
  sido_name: string | null;
  score: number;
  rank: number;
  grade: number;
  grade_label: string;
  percentile: number;
  sido_rank: number | null;
  sido_count: number | null;
  pipe_total_length_km: number | null;
  /** 최근 강수 반영 점수 (score × 최대 1.2). 순위·등급에는 쓰지 않습니다. */
  triggered_score: number | null;
}

export interface PriorityScoreDetail extends PriorityScoreSummary {
  batch_id: string;
  model_version: string;
  calculated_at: string;
  features: Record<string, number | null>;
  contributions: Record<string, number>;
  contribution_shares: Record<string, number>;
  // 결측 지표는 전 구역 공통 중앙값 패딩이므로 회색 처리해야 합니다.
  missing_features: string[];
  display_names: Record<string, string>;
}

export interface PriorityListResponse {
  meta: PriorityMeta | null;
  total: number;
  districts: PriorityScoreSummary[];
}

export interface PrioritySidoSummary {
  sido_code: string;
  sido_name: string | null;
  rank: number;
  district_count: number;
  // 관로 연장 가중 평균 (단순 평균이면 시군구 수가 적은 시도가 왜곡됨)
  weighted_score: number;
  top_district: string | null;
  grade_counts: Record<string, number>;
}

export type PriorityBatchStatus = 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED';

export interface PriorityBatch {
  batch_id: string;
  engine_batch_id: string | null;
  status: PriorityBatchStatus;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  district_count: number | null;
  run_engine: boolean;
  reason: string | null;
  error: string | null;
  /** 입력 지문이 직전 배치와 달랐는지. false 면 순위가 같은 것이 정상입니다. */
  input_changed: boolean | null;
  changed_ranks: number | null;
  max_rank_shift: number | null;
  previous_batch_id: string | null;
}

export interface PriorityRecalculateAccepted {
  batch_id: string;
  status: PriorityBatchStatus;
  status_url: string;
  queued_at: string;
}

export interface MunicipalityFeature {
  type: 'Feature';
  properties: { code: string; sido_name: string; name: string };
  geometry: {
    type: 'Polygon' | 'MultiPolygon';
    coordinates: number[][][] | number[][][][];
  };
}

export interface MunicipalityGeojson {
  type: 'FeatureCollection';
  features: MunicipalityFeature[];
}

export function getPriorityDistricts(
  params?: { sido?: string; grade?: number; limit?: number },
  signal?: AbortSignal,
): Promise<PriorityListResponse> {
  const query = new URLSearchParams();
  if (params?.sido) query.set('sido', params.sido);
  if (params?.grade != null) query.set('grade', String(params.grade));
  // 기본값을 229 로 박지 않습니다 — 행정구역 수는 데이터의 사실이지 코드의
  // 상수가 아닙니다. 서버 상한(1000)만큼 요청해 잘림을 피합니다.
  query.set('limit', String(params?.limit ?? 1000));
  return apiRequest(`/priority/districts?${query.toString()}`, { signal });
}

export function getPrioritySido(
  signal?: AbortSignal,
): Promise<PrioritySidoSummary[]> {
  return apiRequest('/priority/sido', { signal });
}

export function getPriorityDistrictDetail(
  district: string,
  signal?: AbortSignal,
): Promise<PriorityScoreDetail> {
  return apiRequest(`/priority/districts/${district}`, { signal });
}

// 경계 1.28 MB 는 배치가 바뀌어도 불변이라 모듈 수명 동안 캐시합니다.
// 컴포넌트 캐시(state)는 탭을 떠나 언마운트되면 사라져 재방문마다 다시 받습니다.
let municipalityGeojsonCache: MunicipalityGeojson | null = null;

export async function getPriorityGeojson(
  signal?: AbortSignal,
): Promise<MunicipalityGeojson> {
  if (municipalityGeojsonCache) return municipalityGeojsonCache;
  // Content-Type 이 application/geo+json 이라 apiRequest 가 text 로 돌려줍니다.
  const response = await apiRequest<string | MunicipalityGeojson>(
    '/priority/districts/geojson',
    { signal },
  );
  municipalityGeojsonCache =
    typeof response === 'string' ? JSON.parse(response) : response;
  return municipalityGeojsonCache as MunicipalityGeojson;
}

export interface SidoBoundaryGeojson {
  type: 'FeatureCollection';
  features: Array<{
    type: 'Feature';
    properties: { code: string; sido_name: string; name: string };
    geometry: { type: 'Polygon' | 'MultiPolygon'; coordinates: unknown };
  }>;
}

// 시도 경계선(17개 폴리곤) — 시군구 경계와 마찬가지로 불변이라 모듈 캐시.
let sidoBoundaryCache: SidoBoundaryGeojson | null = null;

export async function getSidoBoundaryGeojson(
  signal?: AbortSignal,
): Promise<SidoBoundaryGeojson> {
  if (sidoBoundaryCache) return sidoBoundaryCache;
  const response = await apiRequest<string | SidoBoundaryGeojson>(
    '/priority/sido/geojson',
    { signal },
  );
  sidoBoundaryCache =
    typeof response === 'string' ? JSON.parse(response) : response;
  return sidoBoundaryCache as SidoBoundaryGeojson;
}

export interface AdminDong {
  adm_code: string;
  adm_name: string;
  sgg_code: string;
  subsidence_count: number;
  sewer_cause_count: number;
}

// Level 2 는 점수화하지 않습니다 — 읍면동 단위 지표가 침하 이력뿐.
export interface DongListResponse {
  sgg_code: string;
  full_name: string;
  total: number;
  // 동을 특정하지 못한 침하 건수. 0 이 아니면 "이력 없음"이 단정이 아니라는
  // 뜻이므로 화면이 반드시 함께 말해야 합니다.
  unlocated_count: number;
  dongs: AdminDong[];
}

export function getDistrictDongs(
  district: string,
  signal?: AbortSignal,
): Promise<DongListResponse> {
  return apiRequest(`/priority/districts/${district}/dongs`, { signal });
}

export interface DongGeojson {
  type: 'FeatureCollection';
  features: Array<{
    type: 'Feature';
    properties: {
      adm_code: string;
      sgg_code: string;
      sgg_name: string;
      adm_name: string;
    };
    geometry: { type: 'Polygon' | 'MultiPolygon'; coordinates: unknown };
  }>;
}

// 전국 6.2 MB 라 시군구 단위로 잘라 받습니다.
export async function getDongGeojson(
  sgg: string,
  signal?: AbortSignal,
): Promise<DongGeojson> {
  const response = await apiRequest<string | DongGeojson>(
    `/priority/dongs/geojson?sgg=${encodeURIComponent(sgg)}`,
    { signal },
  );
  return typeof response === 'string' ? JSON.parse(response) : response;
}

export interface PriorityHistoryPoint {
  batch_id: string;
  calculated_at: string;
  model_version: string;
  score: number;
  rank: number;
  grade: number;
}

export interface PriorityInspectionSummary {
  session_id: number;
  started_at: string | null;
  status: string | null;
  defect_count: number;
}

export interface PriorityHistoryResponse {
  district_id: number;
  sgg_code: string;
  full_name: string;
  points: PriorityHistoryPoint[];
  // 산정(예측) 옆의 실측 — 이 구역에서 실행된 로봇 점검 세션과 결함 수.
  inspections: PriorityInspectionSummary[];
}

export function getPriorityHistory(
  district: string,
  signal?: AbortSignal,
): Promise<PriorityHistoryResponse> {
  return apiRequest(`/priority/history?district=${encodeURIComponent(district)}`, {
    signal,
  });
}

export function recalculatePriority(
  runEngine: boolean,
  reason?: string,
): Promise<PriorityRecalculateAccepted> {
  return apiRequest('/priority/recalculate', {
    method: 'POST',
    body: JSON.stringify({ run_engine: runEngine, reason: reason ?? null }),
  });
}

export function getPriorityBatch(
  batchId: string,
  signal?: AbortSignal,
): Promise<PriorityBatch> {
  return apiRequest(`/priority/batches/${encodeURIComponent(batchId)}`, {
    signal,
  });
}
