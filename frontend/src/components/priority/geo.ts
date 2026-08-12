import type { Feature, FeatureCollection, Geometry } from 'geojson';
import type {
  DongGeojson,
  MunicipalityGeojson,
  PriorityScoreSummary,
} from '../../api/priority';

// 등급 1→5 순차 램프 (단일 색상, 밝음→어두움). 등급은 크기이므로 색상환을
// 돌리지 않고 한 색의 명도만 씁니다. 5(최우선)가 가장 어둡습니다.
export const GRADE_COLORS: Record<number, string> = {
  1: '#86b6ef',
  2: '#5598e7',
  3: '#2a78d6',
  4: '#1c5cab',
  5: '#0d366b',
};
export const MISSING_BAR = '#898781';
export const INACTIVE_FILL = '#d8d8d4';

// 엔진 GRADE_LABELS(scoring/grading.py)와 동일해야 합니다.
export const GRADE_LABELS: Record<number, string> = {
  1: '양호',
  2: '관찰',
  3: '보통',
  4: '우선',
  5: '최우선',
};

// 전국 순위를 공식 등급과 같은 상대배분(10/20/40/20/10)으로 접습니다.
// 강수 반영 보기에서 지도 색·등급 배지가 같은 값을 쓰도록 한 곳에 둡니다.
export function foldRankToGrade(rank: number, total: number): number {
  const pct = rank / total;
  return pct <= 0.1 ? 5 : pct <= 0.3 ? 4 : pct <= 0.7 ? 3 : pct <= 0.9 ? 2 : 1;
}

// 배경지도는 두 겹입니다.
//
// ① 전 줌: CARTO light_nolabels — 지명이 **없는** 무채색 타일. 시도·시군구
//    이름은 우리 GeoJSON 으로 직접 그립니다(아래 라벨 레이어). 라벨 있는
//    타일을 못 쓰는 이유: 브이월드·OSM 은 2026-07-01 개편 후 지도라
//    "전남광주통합특별시"가 z6~z10 타일에 구워져 있는데(실측), 우리 데이터는
//    개편 전 기준이라 지도 표기와 데이터가 서로 모순됩니다.
// ② z11 이상(동 뷰): 브이월드 Base 페이드인 — 역·저수지·면 이름 같은 국지
//    컨텍스트가 여기서만 나오고, z11 부터는 개편 시도명 라벨이 없습니다(실측).
//
// 브이월드 주의점: 경로가 {z}/{y}/{x} (x·y 뒤바뀜), 유효 스타일은
// Base·midnight·Hybrid ('gray' 는 404), 배포가 https 라 https 고정.
// 키가 없으면 ② 만 빠지고 지도는 그대로 뜹니다.
const VWORLD_KEY = import.meta.env.VITE_VWORLD_KEY;

export const BASEMAP_TILES = ['a', 'b', 'c'].map(
  (subdomain) =>
    `https://${subdomain}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png`,
);

export const DETAIL_TILES: string[] | null = VWORLD_KEY
  ? [`https://api.vworld.kr/req/wmts/1.0.0/${VWORLD_KEY}/Base/{z}/{y}/{x}.png`]
  : null;

// z10 타일까지 개편 시도명이 박혀 있어서, z11 타일만 쓰이는 줌부터 페이드인
// 합니다 (256px 래스터는 줌 10.5 부터 z11 타일을 고릅니다).
export const DETAIL_MIN_ZOOM = 10.5;
export const DETAIL_FULL_ZOOM = 11.3;

export const ATTRIBUTION = VWORLD_KEY
  ? '© CARTO © OpenStreetMap contributors · © 국토교통부 브이월드'
  : '© CARTO © OpenStreetMap contributors';

// 브이월드는 바다·녹지에 색이 있어 파란 등급 램프와 경쟁합니다. 채도를 크게
// 낮춰 배경을 무채색에 가깝게 만듭니다.
export const BASEMAP_SATURATION = -0.85;

// 한반도 밖으로 나갈 이유가 없습니다. 이동·줌을 데이터 bbox 주변으로 제한하되,
// 여백을 너무 조이면 전국 화면 자체가 잘려서 fitBounds 가 클램프됩니다.
//
// 경도 여백이 왜 이렇게 큰가 — 데이터 bbox 는 lon 7.26° × lat 5.51° 로, 머케이터에서
// 거의 정사각(세로/가로 0.94)입니다. 그런데 지도 틀은 가로로 넓어서(대개 2:1 안팎)
// 위도를 다 담으려면 **경도를 15° 넘게** 보여 줘야 합니다. maxBounds 가 경도로
// 9.66°(7.26 + 1.2×2)만 허용하면 MapLibre 가 그 안에 맞추려고 줌을 당기고, 그 결과
// 남쪽 끝인 **제주도가 화면 밖으로 잘려 나갑니다.**
//
// 세로로 넓은 화면은 반대로 위도가 남으므로 위도 여백은 조금만 있으면 됩니다.
export const MAX_BOUNDS_MARGIN_LON = 7.0;
export const MAX_BOUNDS_MARGIN_LAT = 1.5;
export const MIN_ZOOM = 4.8;

// 읍면동(점수 미산정) 채움색 — 등급 램프와 확실히 다른 중립 청회색.
export const DONG_FILL = '#5b7ca8';

export const EMPTY_FC: FeatureCollection = {
  type: 'FeatureCollection',
  features: [],
};

// 시도 17개를 순위 기준 5단계로 접습니다. 1위(위험 최고)가 가장 어두운 색.
export function sidoColor(rank: number, total: number): string {
  const bucket = Math.min(5, Math.max(1, Math.ceil((rank / total) * 5)));
  return GRADE_COLORS[6 - bucket];
}

export type LngLatBounds = [[number, number], [number, number]];

// 시도 라벨 위치 수동 지정 — 자동(면적 가중 평균)이 어긋나는 곳만.
// 시도는 17개 고정 집합이라 좌표를 박는 쪽이 휴리스틱을 더 쌓는 것보다 낫습니다.
const SIDO_LABEL_OVERRIDES: Record<string, [number, number]> = {
  // 경기 — 평균점 주변이 서울·인천 라벨과 충돌해 통째로 숨습니다. 동쪽으로.
  '41': [127.33, 37.5],
  // 인천 — 면적 큰 강화군이 평균을 북쪽으로 끌어올립니다. 본토(연수·남동)로.
  '28': [126.68, 37.41],
  // 전남 — 섬 시군구들이 평균을 서해안으로 끌어냅니다. 내륙(영암·장흥)으로.
  '46': [126.93, 34.82],
  // 대전 — 세종 라벨에 밀려 서쪽 경계로 붙습니다. 도심으로.
  '30': [127.42, 36.33],
};

// 짝수-홀수 레이 캐스팅. 시도 라벨 평균점이 도형 안인지 판정할 때만 씁니다.
function pointInRing(x: number, y: number, ring: number[][]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

// 그 시도의 어느 시군구 외곽 링이든 점을 품으면 참. 구멍(홀)은 무시하는
// 근사지만, 시군구 단위에서 홀은 사실상 없습니다.
function pointInSido(
  geo: MunicipalityGeojson,
  sido: string,
  lon: number,
  lat: number,
): boolean {
  for (const feature of geo.features) {
    if (feature.properties.code.slice(0, 2) !== sido) continue;
    const outers: number[][][] =
      feature.geometry.type === 'Polygon'
        ? [(feature.geometry.coordinates as number[][][])[0]]
        : (feature.geometry.coordinates as number[][][][]).map(
            (polygon) => polygon[0],
          );
    for (const ring of outers) {
      if (pointInRing(lon, lat, ring)) return true;
    }
  }
  return false;
}

export interface EnrichedData {
  geojson: FeatureCollection;
  koreaBounds: LngLatBounds;
  sidoBounds: Map<string, LngLatBounds>;
  districtBounds: Map<string, LngLatBounds>;
  sidoLabels: FeatureCollection;
  sggLabels: FeatureCollection;
}

// GeoJSON 에 색·시도코드를 심고, 시도·시군구별 bbox 와 라벨 앵커를 뽑습니다.
// 시도 경계선은 여기서 만들지 않습니다 — 단순화가 시군구별 독립이라 인접
// 폴리곤이 꼭짓점을 공유하지 않아, 공유 변 매칭은 점선 조각만 남습니다.
// 엔진이 dissolve 해서 내보내는 sido_boundaries.geojson 을 씁니다.
export function enrich(
  geo: MunicipalityGeojson,
  scores: Map<string, PriorityScoreSummary>,
  sidoRank: Map<string, number>,
  sidoTotal: number,
): EnrichedData {
  let minLon = Infinity;
  let maxLon = -Infinity;
  let minLat = Infinity;
  let maxLat = -Infinity;
  const sidoBox = new Map<string, [number, number, number, number]>();
  const districtBox = new Map<string, [number, number, number, number]>();
  // 라벨 앵커: 시군구마다 가장 큰 링(본토 폴리곤)의 꼭짓점 평균. bbox 중심을
  // 쓰면 섬이 많은 시군구(여수 등)에서 라벨이 바다에 떨어집니다.
  const labelAnchor = new Map<
    string,
    { area: number; lon: number; lat: number; name: string; sido: string; sidoName: string }
  >();

  // 강수 반영 색 — 사전 등록된 배율 공식(triggered_score)의 전국 순위를 공식
  // 등급과 같은 상대배분(10/20/40/20/10)으로 접습니다. 공식 등급이 아니므로
  // 지도를 "강수 반영 보기"로 전환했을 때만 씁니다.
  const triggeredRankByCode = new Map<string, number>();
  [...scores.entries()]
    .filter(([, s]) => s.triggered_score != null)
    .sort(([, a], [, b]) => (b.triggered_score ?? 0) - (a.triggered_score ?? 0))
    .forEach(([code], index) => triggeredRankByCode.set(code, index + 1));
  const triggeredTotal = triggeredRankByCode.size;
  const triggeredColorOf = (code: string): string | null => {
    const rank = triggeredRankByCode.get(code);
    if (rank == null || triggeredTotal === 0) return null;
    return GRADE_COLORS[foldRankToGrade(rank, triggeredTotal)];
  };

  const features: Feature[] = geo.features.map((feature, index) => {
    const code = feature.properties.code;
    const sido = code.slice(0, 2);
    const score = scores.get(code);
    const rank = sidoRank.get(sido);

    const rings: number[][][] =
      feature.geometry.type === 'Polygon'
        ? (feature.geometry.coordinates as number[][][])
        : (feature.geometry.coordinates as number[][][][]).flat();

    let box = sidoBox.get(sido);
    let own: [number, number, number, number] | undefined;
    for (const ring of rings) {
      let ringMinLon = Infinity;
      let ringMaxLon = -Infinity;
      let ringMinLat = Infinity;
      let ringMaxLat = -Infinity;
      let sumLon = 0;
      let sumLat = 0;
      for (let i = 0; i < ring.length; i += 1) {
        const [lon, lat] = ring[i];
        if (lon < ringMinLon) ringMinLon = lon;
        if (lon > ringMaxLon) ringMaxLon = lon;
        if (lat < ringMinLat) ringMinLat = lat;
        if (lat > ringMaxLat) ringMaxLat = lat;
        sumLon += lon;
        sumLat += lat;
        if (lon < minLon) minLon = lon;
        if (lon > maxLon) maxLon = lon;
        if (lat < minLat) minLat = lat;
        if (lat > maxLat) maxLat = lat;
        if (!box) box = [lon, lat, lon, lat];
        else {
          if (lon < box[0]) box[0] = lon;
          if (lat < box[1]) box[1] = lat;
          if (lon > box[2]) box[2] = lon;
          if (lat > box[3]) box[3] = lat;
        }
        if (!own) own = [lon, lat, lon, lat];
        else {
          if (lon < own[0]) own[0] = lon;
          if (lat < own[1]) own[1] = lat;
          if (lon > own[2]) own[2] = lon;
          if (lat > own[3]) own[3] = lat;
        }
      }
      // 같은 코드가 여러 피처(비지·섬)로 쪼개져 있어도 가장 큰 링 하나만 남깁니다.
      const ringArea =
        (ringMaxLon - ringMinLon) * (ringMaxLat - ringMinLat);
      const current = labelAnchor.get(code);
      if (ring.length > 0 && (!current || ringArea > current.area)) {
        labelAnchor.set(code, {
          area: ringArea,
          lon: sumLon / ring.length,
          lat: sumLat / ring.length,
          name: feature.properties.name,
          sido,
          sidoName: feature.properties.sido_name,
        });
      }
    }
    if (box) sidoBox.set(sido, box);
    if (own) districtBox.set(code, own);

    return {
      type: 'Feature',
      id: index,
      geometry: feature.geometry as Geometry,
      properties: {
        code,
        sidoCode: sido,
        name: feature.properties.name,
        sidoName: feature.properties.sido_name,
        gradeColor: score ? GRADE_COLORS[score.grade] : INACTIVE_FILL,
        triggeredColor:
          triggeredColorOf(code) ?? (score ? GRADE_COLORS[score.grade] : INACTIVE_FILL),
        sidoColor: rank != null ? sidoColor(rank, sidoTotal) : INACTIVE_FILL,
      },
    };
  });

  const sidoBounds = new Map<string, LngLatBounds>();
  sidoBox.forEach((box, sido) =>
    sidoBounds.set(sido, [
      [box[0], box[1]],
      [box[2], box[3]],
    ]),
  );
  const districtBounds = new Map<string, LngLatBounds>();
  districtBox.forEach((box, code) =>
    districtBounds.set(code, [
      [box[0], box[1]],
      [box[2], box[3]],
    ]),
  );

  // 시군구 라벨: 앵커 그대로. 시도 라벨: 소속 시군구 앵커들의 **평균점**.
  // bbox 중심은 울릉도·독도(경북)나 섬(전남)이 상자를 끌어당겨 치우치고,
  // "중심에서 가장 가까운 시군구" 방식은 그 치우친 중심을 따라갑니다.
  // 평균점이 시도 밖에 떨어지면(경기 — 가운데가 서울) 가장 가까운 시군구
  // 앵커로 폴백합니다.
  const sggLabelFeatures: Feature[] = [];
  const bySido = new Map<
    string,
    { lon: number; lat: number; area: number; name: string }[]
  >();
  labelAnchor.forEach((anchor, code) => {
    sggLabelFeatures.push({
      type: 'Feature',
      properties: { code, sidoCode: anchor.sido, name: anchor.name },
      geometry: { type: 'Point', coordinates: [anchor.lon, anchor.lat] },
    });
    const group = bySido.get(anchor.sido) ?? [];
    group.push({
      lon: anchor.lon,
      lat: anchor.lat,
      area: anchor.area,
      name: anchor.sidoName,
    });
    bySido.set(anchor.sido, group);
  });
  const sidoLabelFeatures: Feature[] = [];
  bySido.forEach((anchors, sido) => {
    if (anchors.length === 0) return;
    const override = SIDO_LABEL_OVERRIDES[sido];
    if (override) {
      sidoLabelFeatures.push({
        type: 'Feature',
        properties: { sidoCode: sido, name: anchors[0].name },
        geometry: { type: 'Point', coordinates: override },
      });
      return;
    }
    // 면적 가중 — 단순 평균이면 섬 시군구(신안·태안 등)가 점을 바다로
    // 끌어내 전남·충남 라벨이 해안에 붙습니다.
    const totalArea = anchors.reduce((sum, a) => sum + a.area, 0) || 1;
    const meanLon =
      anchors.reduce((sum, a) => sum + a.lon * a.area, 0) / totalArea;
    const meanLat =
      anchors.reduce((sum, a) => sum + a.lat * a.area, 0) / totalArea;
    let lon = meanLon;
    let lat = meanLat;
    if (!pointInSido(geo, sido, meanLon, meanLat)) {
      let bestDist = Infinity;
      for (const anchor of anchors) {
        const dist = (anchor.lon - meanLon) ** 2 + (anchor.lat - meanLat) ** 2;
        if (dist < bestDist) {
          bestDist = dist;
          lon = anchor.lon;
          lat = anchor.lat;
        }
      }
    }
    sidoLabelFeatures.push({
      type: 'Feature',
      properties: { sidoCode: sido, name: anchors[0].name },
      geometry: { type: 'Point', coordinates: [lon, lat] },
    });
  });

  return {
    geojson: { type: 'FeatureCollection', features },
    koreaBounds: [
      [minLon, minLat],
      [maxLon, maxLat],
    ],
    sidoBounds,
    districtBounds,
    sidoLabels: { type: 'FeatureCollection', features: sidoLabelFeatures },
    sggLabels: { type: 'FeatureCollection', features: sggLabelFeatures },
  };
}

// ── 읍면동 라벨 ──────────────────────────────────────────────────────────
// 앵커는 "가장 넓은 내부 가로 구간의 가운데"입니다. 꼭짓점 평균을 쓰면 오목한
// 동(초승달·ㄷ자)에서 앵커가 도형 밖으로 나가 이름이 옆 동 위에 찍힙니다
// (전수조사에서 3,557개 중 73개). 이 방식은 앵커가 항상 내부이고, 그 자리에서
// 실제로 쓸 수 있는 폭·높이까지 함께 나와서 라벨이 들어가는지 정확히 잽니다.

export const DONG_LABEL_SIZE = 10.5;

export interface DongLabelBox {
  code: string;
  name: string;
  /** 앵커 위도에서의 내부 가로 구간 [왼쪽, 오른쪽] */
  spanLon: [number, number];
  /** 앵커 경도에서의 내부 세로 구간 [아래, 위] */
  spanLat: [number, number];
  anchor: [number, number];
}

export interface DongLabelData {
  labels: FeatureCollection;
  boxes: DongLabelBox[];
}

// 링을 y = lat 인 수평선으로 잘라 얻은 내부 구간들. 짝수-홀수 규칙이라
// 정렬된 교점을 둘씩 묶으면 구간이 됩니다.
function spansAtLat(ring: number[][], lat: number): Array<[number, number]> {
  const crossings: number[] = [];
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const [x1, y1] = ring[j];
    const [x2, y2] = ring[i];
    if (y1 > lat !== y2 > lat) {
      crossings.push(x1 + ((lat - y1) * (x2 - x1)) / (y2 - y1));
    }
  }
  crossings.sort((a, b) => a - b);
  const spans: Array<[number, number]> = [];
  for (let i = 0; i + 1 < crossings.length; i += 2) {
    spans.push([crossings[i], crossings[i + 1]]);
  }
  return spans;
}

// x = lon 인 수직선으로 자른 뒤 lat 을 품는 구간. 세로 여유를 재는 데 씁니다.
function spanAtLon(ring: number[][], lon: number, lat: number): [number, number] {
  const crossings: number[] = [];
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const [x1, y1] = ring[j];
    const [x2, y2] = ring[i];
    if (x1 > lon !== x2 > lon) {
      crossings.push(y1 + ((lon - x1) * (y2 - y1)) / (x2 - x1));
    }
  }
  crossings.sort((a, b) => a - b);
  for (let i = 0; i + 1 < crossings.length; i += 2) {
    if (crossings[i] <= lat && lat <= crossings[i + 1]) {
      return [crossings[i], crossings[i + 1]];
    }
  }
  return [lat, lat];
}

// 가로선을 여러 높이에서 훑어 가장 넓은 내부 구간을 고릅니다. 11 등분은
// 실측으로 정한 값입니다 — 더 늘려도 앵커가 바뀌는 동이 없었습니다.
const LABEL_SCAN_STEPS = 11;

interface LabelPlacement {
  anchor: [number, number];
  spanLon: [number, number];
  spanLat: [number, number];
}

function labelAnchorOf(ring: number[][]): LabelPlacement | null {
  let minLat = Infinity;
  let maxLat = -Infinity;
  for (const [, lat] of ring) {
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
  }
  let best: { width: number; lon: number; lat: number; spanLon: [number, number] } | null = null;
  for (let step = 1; step < LABEL_SCAN_STEPS; step += 1) {
    const lat = minLat + ((maxLat - minLat) * step) / LABEL_SCAN_STEPS;
    for (const [left, right] of spansAtLat(ring, lat)) {
      const width = right - left;
      if (!best || width > best.width) {
        best = { width, lon: (left + right) / 2, lat, spanLon: [left, right] };
      }
    }
  }
  if (!best) return null;
  return {
    anchor: [best.lon, best.lat],
    spanLon: best.spanLon,
    spanLat: spanAtLon(ring, best.lon, best.lat),
  };
}

export function dongLabels(features: DongGeojson['features']): DongLabelData {
  const labelFeatures: Feature[] = [];
  const boxes: DongLabelBox[] = [];

  for (const feature of features) {
    const rings: number[][][] =
      feature.geometry.type === 'Polygon'
        ? (feature.geometry.coordinates as number[][][])
        : (feature.geometry.coordinates as number[][][][]).flat();

    // 비지·섬으로 쪼개진 동은 가장 큰 조각에만 이름을 답니다.
    let widest: { area: number; ring: number[][] } | null = null;
    for (const ring of rings) {
      if (ring.length === 0) continue;
      let minLon = Infinity;
      let maxLon = -Infinity;
      let minLat = Infinity;
      let maxLat = -Infinity;
      for (const [lon, lat] of ring) {
        if (lon < minLon) minLon = lon;
        if (lon > maxLon) maxLon = lon;
        if (lat < minLat) minLat = lat;
        if (lat > maxLat) maxLat = lat;
      }
      const area = (maxLon - minLon) * (maxLat - minLat);
      if (!widest || area > widest.area) widest = { area, ring };
    }
    if (!widest) continue;
    const placed = labelAnchorOf(widest.ring);
    if (!placed) continue;

    // adm_name 은 "광주광역시 광산구 하남동" 처럼 전체 이름입니다.
    const name = feature.properties.adm_name.split(' ').pop() ?? '';
    labelFeatures.push({
      type: 'Feature',
      properties: { adm_code: feature.properties.adm_code, name },
      geometry: { type: 'Point', coordinates: placed.anchor },
    });
    boxes.push({
      code: feature.properties.adm_code,
      name,
      spanLon: placed.spanLon,
      spanLat: placed.spanLat,
      anchor: placed.anchor,
    });
  }

  return {
    labels: { type: 'FeatureCollection', features: labelFeatures },
    boxes,
  };
}
