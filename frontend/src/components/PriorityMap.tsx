import { useEffect, useMemo, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';

// 프로덕션 번들에서는 maplibre 가 워커 경로를 import.meta.url 기반으로 런타임에
// 조립해서 rollup 이 워커를 청크로 내보내지 못합니다. 그대로 두면
// /assets/maplibre-gl-worker.mjs 가 404 가 되고, 워커가 없으니 GeoJSON 소스가 영영
// 로드되지 않아 **배경지도만 뜨고 시군구 폴리곤이 하나도 그려지지 않습니다.**
// vite.config.ts 의 maplibreWorkerAssets 플러그인이 워커와 shared 청크를
// /maplibre/ 로 복사하므로 그 경로를 알려줍니다.
// 개발 서버는 node_modules 원본을 그대로 서빙해 기본 경로가 맞으니 건드리지 않습니다.
if (!import.meta.env.DEV) {
  maplibregl.setWorkerUrl(`${import.meta.env.BASE_URL}maplibre/maplibre-gl-worker.mjs`);
}
import type { FeatureCollection } from 'geojson';
import { ChevronRight, CloudRain, Map as MapIcon, Play } from 'lucide-react';
import {
  getDistrictDongs,
  getDongGeojson,
  getPriorityDistrictDetail,
  getPriorityDistricts,
  getPriorityGeojson,
  getPriorityHistory,
  getPrioritySido,
  getSidoBoundaryGeojson,
  type AdminDong,
  type DongGeojson,
  type DongListResponse,
  type MunicipalityGeojson,
  type SidoBoundaryGeojson,
  type PriorityHistoryResponse,
  type PriorityListResponse,
  type PriorityScoreDetail,
  type PriorityScoreSummary,
  type PrioritySidoSummary,
} from '../api/priority';
import {
  ATTRIBUTION,
  BASEMAP_SATURATION,
  BASEMAP_TILES,
  DETAIL_FULL_ZOOM,
  DETAIL_MIN_ZOOM,
  DETAIL_TILES,
  DONG_FILL,
  DONG_LABEL_SIZE,
  EMPTY_FC,
  GRADE_COLORS,
  GRADE_LABELS,
  INACTIVE_FILL,
  foldRankToGrade,
  MAX_BOUNDS_MARGIN_LAT,
  MAX_BOUNDS_MARGIN_LON,
  MIN_ZOOM,
  dongLabels,
  enrich,
  type DongLabelBox,
  type EnrichedData,
} from './priority/geo';
import {
  DetailPanel,
  DistrictListPanel,
  DongListPanel,
  SidoListPanel,
} from './priority/Panels';

// 브이월드 상세 타일의 페이드인. 동 뷰에서는 이 값을 0 으로 덮어씁니다 —
// 타일에 동 이름이 구워져 있어서 우리 라벨과 같은 이름이 두 번 겹칩니다.
// 동 이름끼리 최소로 띄울 간격(px). 0 이면 글자가 맞닿아 두 이름이 한 덩어리로
// 읽힙니다.
const LABEL_GAP = 5;

const DETAIL_OPACITY_BY_ZOOM = [
  'interpolate',
  ['linear'],
  ['zoom'],
  DETAIL_MIN_ZOOM,
  0,
  DETAIL_FULL_ZOOM,
  1,
] as unknown as number;

export default function PriorityMap({
  active,
  onStartInspection,
}: {
  active: boolean;
  onStartInspection: (sectionName: string | null, districtId?: number) => void;
}) {
  const [geojson, setGeojson] = useState<MunicipalityGeojson | null>(null);
  const [sidoBoundary, setSidoBoundary] = useState<SidoBoundaryGeojson | null>(null);
  const [list, setList] = useState<PriorityListResponse | null>(null);
  const [sidoList, setSidoList] = useState<PrioritySidoSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  // 드릴다운: null 이면 전국(시도 단위), 값이 있으면 그 시도의 시군구 단위.
  const [viewSido, setViewSido] = useState<string | null>(null);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [detail, setDetail] = useState<PriorityScoreDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  // 3단계: 읍면동 (전국). 경계는 시군구 단위로 잘라 받고 캐시합니다.
  const [dongSgg, setDongSgg] = useState<string | null>(null);
  // 동 선택: 목록·지도 클릭이 공유. 하이라이트 전용이라 상세 조회는 없습니다.
  const [selectedDong, setSelectedDong] = useState<string | null>(null);
  // 강수 반영 보기 — 사전 등록된 배율 공식(triggered_score)의 순위를 지도 색과
  // 목록 순서에 씁니다. 공식 순위·등급은 그대로이므로 참고용 배너를 함께 띄웁니다.
  const [rainMode, setRainMode] = useState(false);
  const rainModeRef = useRef(false);
  const [dongInfo, setDongInfo] = useState<DongListResponse | null>(null);
  const [dongVersion, setDongVersion] = useState(0);
  const [hoveredDong, setHoveredDong] = useState<string | null>(null);
  const dongCacheRef = useRef<Map<string, DongGeojson['features']>>(new Map());
  const dongLabelBoxRef = useRef<DongLabelBox[]>([]);
  const selectedDongRef = useRef<string | null>(null);
  const [history, setHistory] = useState<PriorityHistoryResponse | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [tooltip, setTooltip] = useState<{ x: number; y: number } | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [dongError, setDongError] = useState<string | null>(null);
  const loadedRef = useRef(false);

  const mapBoxRef = useRef<HTMLDivElement | null>(null);
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const mapReadyRef = useRef(false);
  const hoverIdRef = useRef<number | null>(null);
  // 이벤트 핸들러는 map 생성 시 한 번 등록되므로, 최신 상태를 ref 로 봅니다.
  const viewSidoRef = useRef<string | null>(null);
  const dongMapCodeRef = useRef<string | null>(null);
  const selectedCodeRef = useRef<string | null>(null);
  const dongHoverIdRef = useRef<number | null>(null);
  const enrichedRef = useRef<EnrichedData | null>(null);
  // 탭이 처음 열릴 때 한 번만 로드합니다 (GeoJSON 1.28 MB).
  useEffect(() => {
    if (!active || (loadedRef.current && reloadKey === 0)) return;

    const controller = new AbortController();
    const load = async () => {
      try {
        const [geo, boundary, districts, sido] = await Promise.all([
          geojson ? Promise.resolve(geojson) : getPriorityGeojson(controller.signal),
          sidoBoundary
            ? Promise.resolve(sidoBoundary)
            : getSidoBoundaryGeojson(controller.signal),
          // limit 은 api 쪽 기본값(서버 상한)에 맡깁니다. 229 를 박으면
          // 행정구역이 늘었을 때 초과분이 조용히 잘립니다.
          getPriorityDistricts(undefined, controller.signal),
          getPrioritySido(controller.signal),
        ]);
        setGeojson(geo);
        setSidoBoundary(boundary);
        setList(districts);
        setSidoList(sido);
        setLoadError(null);
        // 성공한 뒤에만 세웁니다. 시작할 때 세우면 StrictMode 가 이펙트를
        // 두 번 돌릴 때(마운트→정리→재실행) 첫 요청이 취소된 채로 플래그만
        // 남아서 두 번째 실행이 가드에 걸리고, 화면이 "불러오는 중"에서
        // 영영 멈춥니다. active=true 로 처음부터 마운트되는 셸에서 발생합니다.
        loadedRef.current = true;
      } catch (caught) {
        if (controller.signal.aborted) return;
        setLoadError(
          caught instanceof Error ? caught.message : '우선순위 데이터를 불러오지 못했습니다.',
        );
      }
    };
    void load();
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, reloadKey]);

  // 선택한 시군구 상세 (산정 근거) + 읍면동 유무 + 산정 이력.
  useEffect(() => {
    if (!selectedCode) {
      setDetail(null);
      setDongInfo(null);
      setHistory(null);
      setDetailError(null);
      return;
    }
    const controller = new AbortController();
    setDetailLoading(true);
    setDetailError(null);
    // 직전 선택의 상세·이력을 비웁니다. 남겨두면 빠르게 연타할 때 새 구역
    // 이름 아래 이전 구역의 데이터가 응답 도착까지 그대로 보입니다.
    setDetail(null);
    setHistory(null);
    getPriorityDistrictDetail(selectedCode, controller.signal)
      .then((data) => setDetail(data))
      .catch((caught) => {
        if (!controller.signal.aborted) {
          console.error('[priority] 상세 조회 실패:', caught);
          setDetail(null);
          setDetailError('상세 정보를 불러오지 못했습니다 — 다시 선택해 보세요.');
        }
      })
      .finally(() => {
        // 연타로 취소된 요청의 finally 는 마이크로태스크라 **다음** 선택의
        // 로딩 표시를 끕니다. 취소된 쪽은 손대지 않아야 합니다.
        if (!controller.signal.aborted) setDetailLoading(false);
      });
    // 읍면동 목록 (전국 구축 완료 — 모든 시군구에서 옵니다).
    getDistrictDongs(selectedCode, controller.signal)
      .then((data) => setDongInfo(data))
      .catch(() => {
        if (!controller.signal.aborted) setDongInfo(null);
      });
    // 산정 이력 — 배치가 쌓이면 순위 변화를 보여줍니다.
    getPriorityHistory(selectedCode, controller.signal)
      .then((data) => setHistory(data))
      .catch(() => {
        if (!controller.signal.aborted) setHistory(null);
      });
    return () => controller.abort();
  }, [selectedCode, reloadKey]);

  // 지도에 동을 그리는 기준: 시군구를 고르면 바로 보여 줍니다. dongSgg 는
  // 우측 패널이 동 목록인지만 가립니다 — 지도와 패널을 따로 두는 이유입니다.
  const dongMapCode = dongSgg ?? selectedCode;

  // 동 경계는 시군구 단위로 처음 필요할 때 받아 캐시합니다 (전국 파일이 6.2 MB).
  useEffect(() => {
    if (!dongMapCode) return;
    setDongError(null);
    if (dongCacheRef.current.has(dongMapCode)) return;
    const controller = new AbortController();
    getDongGeojson(dongMapCode, controller.signal)
      .then((geo) => {
        dongCacheRef.current.set(dongMapCode, geo.features);
        setDongVersion((prev) => prev + 1);
      })
      .catch((caught) => {
        if (!controller.signal.aborted) {
          console.error('[priority] 읍면동 경계 조회 실패:', caught);
          // 조용히 시군구 뷰로 돌아가면 사용자는 버튼이 고장난 것으로 압니다.
          setDongSgg(null);
          setDongError('읍면동 경계를 불러오지 못했습니다 — 다시 시도해 주세요.');
        }
      });
    return () => controller.abort();
  }, [dongMapCode]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReadyRef.current || dongVersion === 0) return;
    const features = [...dongCacheRef.current.values()].flat();
    (map.getSource('dongs') as maplibregl.GeoJSONSource | undefined)?.setData({
      type: 'FeatureCollection',
      features,
    } as FeatureCollection);
  }, [dongVersion]);

  const scoreByCode = useMemo(() => {
    const map = new Map<string, PriorityScoreSummary>();
    list?.districts.forEach((district) => map.set(district.sgg_code, district));
    return map;
  }, [list]);

  // 강수까지 반영했을 때의 전국 순위 — 강수 반영 보기의 목록 정렬·요약 카드·
  // 등급 접기(foldRankToGrade)가 전부 이 순위를 씁니다. 평시 화면은 안 봅니다.
  const triggeredRankByCode = useMemo(() => {
    const rows = (list?.districts ?? []).filter(
      (row) => row.triggered_score != null,
    );
    if (rows.length === 0) return new Map<string, number>();
    return new Map(
      [...rows]
        .sort((a, b) => (b.triggered_score ?? 0) - (a.triggered_score ?? 0))
        .map((row, index) => [row.sgg_code, index + 1]),
    );
  }, [list]);

  const sidoByCode = useMemo(() => {
    const map = new Map<string, PrioritySidoSummary>();
    sidoList.forEach((sido) => map.set(sido.sido_code, sido));
    return map;
  }, [sidoList]);

  const enriched = useMemo(() => {
    if (!geojson || !list || sidoList.length === 0) return null;
    const ranks = new Map<string, number>(
      sidoList.map((sido) => [sido.sido_code, sido.rank]),
    );
    return enrich(geojson, scoreByCode, ranks, sidoList.length);
  }, [geojson, list, sidoList, scoreByCode]);
  enrichedRef.current = enriched;

  // ---- MapLibre 초기화 (데이터가 준비된 뒤 한 번)
  useEffect(() => {
    if (!enriched || mapRef.current || !mapContainerRef.current) return;

    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: {
        version: 8,
        // 자체 라벨(시도·시군구 이름)은 전부 한글 음절이라 maplibre 가 로컬
        // 폰트로 그립니다(localIdeographFontFamily 기본 동작 — 한글 음절 범위
        // 포함). 이 glyphs 선언은 심볼 레이어가 요구하는 형식일 뿐, 한글 밖
        // 문자가 없어 실제로 요청되지 않습니다.
        glyphs: `${window.location.origin}/maplibre/glyphs/{fontstack}/{range}.pbf`,
        sources: {
          basemap: {
            type: 'raster',
            tiles: BASEMAP_TILES,
            tileSize: 256,
            attribution: ATTRIBUTION,
          },
          ...(DETAIL_TILES
            ? {
                'basemap-detail': {
                  type: 'raster' as const,
                  tiles: DETAIL_TILES,
                  tileSize: 256,
                },
              }
            : {}),
        },
        layers: [
          // 기본 배경은 라벨 없는 무채색 타일(CARTO nolabels) — 지명은 우리
          // 데이터(개편 전 기준)로 직접 그립니다. 이유는 geo.ts 주석 참고.
          { id: 'basemap', type: 'raster', source: 'basemap' },
          ...(DETAIL_TILES
            ? [
                {
                  id: 'basemap-detail',
                  type: 'raster' as const,
                  source: 'basemap-detail',
                  // 동 뷰 줌부터 브이월드 페이드인 — 역·면 이름 같은 국지
                  // 컨텍스트를 살립니다. z10 이하 타일에는 개편 후 시도명이
                  // 박혀 있어(실측) 그 아래 줌에서는 요청조차 하지 않습니다.
                  minzoom: DETAIL_MIN_ZOOM,
                  paint: {
                    'raster-opacity': DETAIL_OPACITY_BY_ZOOM,
                    // 브이월드는 바다·녹지가 파래서 등급 램프와 경쟁 — 채도만
                    // 죽입니다. 지명 가독성은 명도가 결정하므로 글자는 남습니다.
                    'raster-saturation': BASEMAP_SATURATION,
                  },
                },
              ]
            : []),
        ],
      },
      bounds: enriched.koreaBounds,
      fitBoundsOptions: { padding: 24 },
      maxBounds: [
        [
          enriched.koreaBounds[0][0] - MAX_BOUNDS_MARGIN_LON,
          enriched.koreaBounds[0][1] - MAX_BOUNDS_MARGIN_LAT,
        ],
        [
          enriched.koreaBounds[1][0] + MAX_BOUNDS_MARGIN_LON,
          enriched.koreaBounds[1][1] + MAX_BOUNDS_MARGIN_LAT,
        ],
      ],
      minZoom: MIN_ZOOM,
      dragRotate: false,
      pitchWithRotate: false,
      // 클릭이 곧 선택인 지도라 더블클릭 줌을 끕니다 — 더블클릭이 click 을
      // 두 번 발화시켜 "선택 → 해제"가 일어난 채 줌만 남습니다. 줌은 휠·버튼으로.
      doubleClickZoom: false,
      attributionControl: { compact: true },
    });
    map.touchZoomRotate.disableRotation();
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }));
    mapRef.current = map;

    map.on('load', () => {
      map.addSource('districts', { type: 'geojson', data: enriched.geojson });
      // 시도 폴리곤 17개 — 라인 레이어가 외곽선만 그립니다. 시군구 경계에서
      // 못 만드는 이유는 geo.ts 의 enrich 주석 참고.
      map.addSource('sido-boundary', {
        type: 'geojson',
        data: (sidoBoundary ?? EMPTY_FC) as FeatureCollection,
      });

      // 채우기: 전국 뷰에서는 시도색. 호버는 투명도만 살짝 — 두꺼운 외곽선은
      // 과해서 쓰지 않습니다.
      map.addLayer({
        id: 'districts-fill',
        type: 'fill',
        source: 'districts',
        paint: {
          'fill-color': ['get', 'sidoColor'],
          'fill-opacity': [
            'case',
            ['boolean', ['feature-state', 'hover'], false],
            0.88,
            0.72,
          ],
        },
      });
      // 시군구 경계(흰 실선): 드릴다운한 시도 안에서만. 전국 뷰는 시도
      // 경계(검정)만 남겨 시도 덩어리로 읽히게 합니다.
      map.addLayer({
        id: 'districts-line',
        type: 'line',
        source: 'districts',
        paint: { 'line-color': '#ffffff', 'line-width': 0.8 },
        filter: ['==', ['get', 'sidoCode'], '__none__'],
      });
      // 시도 경계: 항상 표시 — 비슷한 색의 인접 시도를 구분합니다.
      // 폴리곤 외곽선이라 연속선이고, 해안선·섬 둘레도 함께 그려집니다.
      map.addLayer({
        id: 'sido-line',
        type: 'line',
        source: 'sido-boundary',
        layout: { 'line-join': 'round' },
        paint: {
          'line-color': '#3f3e3b',
          // 전국 뷰에서 섬 둘레까지 1.8px 이면 남해안이 새까매집니다.
          'line-width': ['interpolate', ['linear'], ['zoom'], 5.5, 1.0, 9, 1.8],
          'line-opacity': 0.95,
        },
      });
      // 선택 표시: 흰색 가는 선 (검고 두꺼운 강조는 과함).
      map.addLayer({
        id: 'selected-line',
        type: 'line',
        source: 'districts',
        paint: { 'line-color': '#ffffff', 'line-width': 2.2 },
        filter: ['==', ['get', 'code'], '__none__'],
      });

      // 읍면동 (Level 2). 점수를 내지 않으므로 등급 램프가 아닌 중립색 하나로,
      // 경계·이름·침하 이력만 보여줍니다. 데이터는 동 뷰에 처음 들어갈 때 채웁니다.
      map.addSource('dongs', { type: 'geojson', data: EMPTY_FC, generateId: true });
      map.addLayer({
        id: 'dongs-fill',
        type: 'fill',
        source: 'dongs',
        paint: {
          'fill-color': DONG_FILL,
          'fill-opacity': [
            'case',
            ['boolean', ['feature-state', 'hover'], false],
            0.42,
            0.22,
          ],
        },
        filter: ['==', ['get', 'sgg_code'], '__none__'],
      });
      map.addLayer({
        id: 'dongs-line',
        type: 'line',
        source: 'dongs',
        paint: { 'line-color': '#ffffff', 'line-width': 1.0 },
        filter: ['==', ['get', 'sgg_code'], '__none__'],
      });
      // 동 선택 표시: 목록이나 지도에서 고른 동을 채움+파란 외곽선으로 밝힙니다.
      map.addLayer({
        id: 'dong-selected-fill',
        type: 'fill',
        source: 'dongs',
        paint: { 'fill-color': DONG_FILL, 'fill-opacity': 0.45 },
        filter: ['==', ['get', 'adm_code'], '__none__'],
      });
      map.addLayer({
        id: 'dong-selected-line',
        type: 'line',
        source: 'dongs',
        paint: { 'line-color': '#0f458f', 'line-width': 2.4 },
        filter: ['==', ['get', 'adm_code'], '__none__'],
      });

      // 지명 라벨 — 우리 데이터(개편 전 기준)로 직접 그립니다. 전국 뷰는 시도
      // 이름, 드릴다운 뒤에는 그 시도의 시군구 이름. 배경 타일에 지명이 없어
      // 이 레이어가 유일한 지명원입니다.
      map.addSource('sido-labels', { type: 'geojson', data: enriched.sidoLabels });
      map.addSource('sgg-labels', { type: 'geojson', data: enriched.sggLabels });
      // 읍면동 이름. 어느 동을 그릴지는 매 이동마다 픽셀로 판정합니다
      // (좁은 동은 이름이 구역을 넘칩니다) — refreshDongLabels 참고.
      map.addSource('dong-labels', { type: 'geojson', data: EMPTY_FC });
      map.addLayer({
        id: 'dong-labels',
        type: 'symbol',
        source: 'dong-labels',
        filter: ['==', ['get', 'adm_code'], '__none__'],
        layout: {
          'text-field': ['get', 'name'],
          'text-size': DONG_LABEL_SIZE,
          'text-font': ['Noto Sans Regular'],
          'text-padding': 2,
        },
        paint: {
          'text-color': '#33383f',
          'text-halo-color': 'rgba(255,255,255,0.92)',
          'text-halo-width': 1.4,
        },
      });
      map.addLayer({
        id: 'sgg-labels',
        type: 'symbol',
        source: 'sgg-labels',
        filter: ['==', ['get', 'sidoCode'], '__none__'],
        layout: {
          'text-field': ['get', 'name'],
          'text-size': 11.5,
          'text-font': ['Noto Sans Regular'],
          'text-padding': 4,
        },
        paint: {
          'text-color': '#3b3e45',
          'text-halo-color': 'rgba(255,255,255,0.9)',
          'text-halo-width': 1.2,
        },
      });
      map.addLayer({
        id: 'sido-labels',
        type: 'symbol',
        source: 'sido-labels',
        layout: {
          'text-field': ['get', 'name'],
          'text-size': 13,
          'text-font': ['Noto Sans Bold'],
          'text-padding': 2,
          // 수도권처럼 앵커가 몰린 곳은 가운데 고정이면 서로 충돌해 통째로
          // 숨습니다(경기도가 사라짐). 밀려날 자리를 줍니다.
          'text-variable-anchor': ['center', 'top', 'bottom', 'left', 'right'],
          'text-radial-offset': 0.8,
          'text-justify': 'auto',
        },
        paint: {
          'text-color': '#2f3238',
          'text-halo-color': 'rgba(255,255,255,0.9)',
          'text-halo-width': 1.6,
        },
      });

      map.on('mousemove', 'dongs-fill', (event) => {
        const feature = event.features?.[0];
        if (!feature) return;
        map.getCanvas().style.cursor = 'default';
        if (dongHoverIdRef.current !== null && dongHoverIdRef.current !== feature.id) {
          map.setFeatureState(
            { source: 'dongs', id: dongHoverIdRef.current },
            { hover: false },
          );
        }
        dongHoverIdRef.current = feature.id as number;
        map.setFeatureState({ source: 'dongs', id: feature.id }, { hover: true });
        setHoveredDong(String(feature.properties.adm_code));
        setTooltip({ x: event.point.x, y: event.point.y });
      });
      map.on('mouseleave', 'dongs-fill', () => {
        map.getCanvas().style.cursor = '';
        if (dongHoverIdRef.current !== null) {
          map.setFeatureState(
            { source: 'dongs', id: dongHoverIdRef.current },
            { hover: false },
          );
          dongHoverIdRef.current = null;
        }
        setHoveredDong(null);
        setTooltip(null);
      });
      // 동 클릭 = 선택 토글. 목록 클릭과 같은 상태를 공유해 양쪽이 함께 켜집니다.
      // 우측 패널도 동 목록으로 맞춥니다 — 지도에서 고른 동의 침하 건수를
      // 보려고 "읍면동 N개 보기"를 한 번 더 누르게 하면 안 됩니다.
      map.on('click', 'dongs-fill', (event) => {
        const feature = event.features?.[0];
        if (!feature) return;
        const code = String(feature.properties.adm_code);
        if (selectedDongRef.current === code) {
          // 같은 동 재클릭 = 취소. 동 목록도 접고 시군구 상세로 돌아갑니다.
          setSelectedDong(null);
          setDongSgg(null);
          return;
        }
        if (dongMapCodeRef.current) setDongSgg(dongMapCodeRef.current);
        setSelectedDong(code);
      });

      map.on('mousemove', 'districts-fill', (event) => {
        const feature = event.features?.[0];
        if (!feature) return;
        map.getCanvas().style.cursor = 'pointer';

        if (hoverIdRef.current !== null && hoverIdRef.current !== feature.id) {
          map.setFeatureState(
            { source: 'districts', id: hoverIdRef.current },
            { hover: false },
          );
        }
        hoverIdRef.current = feature.id as number;
        map.setFeatureState({ source: 'districts', id: feature.id }, { hover: true });

        setHovered(String(feature.properties.code));
        setTooltip({ x: event.point.x, y: event.point.y });
      });
      map.on('mouseleave', 'districts-fill', () => {
        map.getCanvas().style.cursor = '';
        if (hoverIdRef.current !== null) {
          map.setFeatureState(
            { source: 'districts', id: hoverIdRef.current },
            { hover: false },
          );
          hoverIdRef.current = null;
        }
        setHovered(null);
        setTooltip(null);
      });
      map.on('click', 'districts-fill', (event) => {
        const feature = event.features?.[0];
        if (!feature) return;
        const code = String(feature.properties.code);
        const sido = String(feature.properties.sidoCode);
        if (dongMapCodeRef.current) {
          // 동 뷰: 그 시군구 안 클릭은 동 레이어 몫, 다른 시군구 클릭은 동 뷰를
          // 닫고 그쪽으로 이동.
          if (code === dongMapCodeRef.current) return;
          setDongSgg(null);
        }
        if (viewSidoRef.current === sido) {
          // 이미 그 시도 안: 시군구 선택/해제.
          setSelectedCode((prev) => (prev === code ? null : code));
        } else {
          // 전국 뷰이거나 다른 시도를 클릭: 그 시도로 드릴다운.
          drillInto(sido);
        }
      });

      mapReadyRef.current = true;
      // load 전에 우측 패널로 드릴다운했을 수 있으므로 현재 뷰를 적용합니다.
      // null 을 박으면 UI 는 시도 뷰인데 지도만 전국으로 남습니다.
      applyLevel(map, viewSidoRef.current);

      // 개발 모드 전용: 헤드리스 브라우저 테스트가 좌표→픽셀 변환에 씁니다.
      if (import.meta.env.DEV) {
        (window as unknown as Record<string, unknown>).__priorityMap = map;
      }
    });

    return () => {
      mapReadyRef.current = false;
      map.remove();
      mapRef.current = null;
      // remove() 된 지도가 전역 참조로 GC 를 못 받지 않게 정리합니다.
      if (import.meta.env.DEV) {
        delete (window as unknown as Record<string, unknown>).__priorityMap;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enriched === null]);

  // 재산정 뒤 데이터 갱신: 소스만 교체.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReadyRef.current || !enriched) return;
    (map.getSource('districts') as maplibregl.GeoJSONSource | undefined)?.setData(
      enriched.geojson,
    );
    (map.getSource('sido-labels') as maplibregl.GeoJSONSource | undefined)?.setData(
      enriched.sidoLabels,
    );
    (map.getSource('sgg-labels') as maplibregl.GeoJSONSource | undefined)?.setData(
      enriched.sggLabels,
    );
  }, [enriched]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReadyRef.current || !sidoBoundary) return;
    (map.getSource('sido-boundary') as maplibregl.GeoJSONSource | undefined)?.setData(
      sidoBoundary as FeatureCollection,
    );
  }, [sidoBoundary]);

  // 탭 전환 시 컨테이너 크기가 0→실크기로 바뀌므로 리사이즈를 알립니다.
  useEffect(() => {
    if (active) mapRef.current?.resize();
  }, [active]);

  // 시군구 채움색. 시도 뷰에서 하나를 선택하면 나머지는 회색으로 눕혀
  // 선택만 등급색을 유지합니다 — 색이 "지금 보는 것"만 가리키게.
  const applyDistrictFill = (
    map: maplibregl.Map,
    sido: string | null,
    selected: string | null,
  ) => {
    // 강수 반영 보기: 등록된 배율 공식의 순위로 칠한 색(triggeredColor).
    // 공식 등급색과 같은 램프를 쓰되 어디까지나 참고용 — 켠 동안 배너가 뜹니다.
    const fill = rainModeRef.current ? 'triggeredColor' : 'gradeColor';
    if (!sido) {
      map.setPaintProperty('districts-fill', 'fill-color', ['get', 'sidoColor']);
    } else if (selected) {
      map.setPaintProperty('districts-fill', 'fill-color', [
        'case',
        ['==', ['get', 'code'], selected],
        ['get', fill],
        INACTIVE_FILL,
      ]);
    } else {
      map.setPaintProperty('districts-fill', 'fill-color', [
        'case',
        ['==', ['get', 'sidoCode'], sido],
        ['get', fill],
        INACTIVE_FILL,
      ]);
    }
  };

  // 레벨(전국/시도)이 바뀌면 색·경계·카메라를 갱신합니다.
  const applyLevel = (map: maplibregl.Map, sido: string | null) => {
    if (!mapReadyRef.current) return;
    const data = enrichedRef.current;
    applyDistrictFill(map, sido, sido ? selectedCodeRef.current : null);
    if (sido) {
      map.setPaintProperty('districts-fill', 'fill-opacity', [
        'case',
        ['!=', ['get', 'sidoCode'], sido],
        0.25,
        ['boolean', ['feature-state', 'hover'], false],
        0.9,
        0.75,
      ]);
      map.setFilter('districts-line', ['==', ['get', 'sidoCode'], sido]);
      map.setLayoutProperty('sido-labels', 'visibility', 'none');
      map.setFilter('sgg-labels', ['==', ['get', 'sidoCode'], sido]);
      const bounds = data?.sidoBounds.get(sido);
      if (bounds) map.fitBounds(bounds, { padding: 48, duration: 700 });
    } else {
      // 전국 뷰 호버는 아래 별도 이펙트가 시도 단위로 처리합니다.
      map.setPaintProperty('districts-fill', 'fill-opacity', 0.72);
      map.setFilter('districts-line', ['==', ['get', 'sidoCode'], '__none__']);
      map.setLayoutProperty('sido-labels', 'visibility', 'visible');
      map.setFilter('sgg-labels', ['==', ['get', 'sidoCode'], '__none__']);
      if (data) map.fitBounds(data.koreaBounds, { padding: 24, duration: 700 });
    }
  };

  // 전국 뷰 호버: 시군구 하나가 아니라 그 시도 전체를 밝힙니다.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReadyRef.current || viewSido) return;
    const sido = hovered ? hovered.slice(0, 2) : '__none__';
    map.setPaintProperty('districts-fill', 'fill-opacity', [
      'case',
      ['==', ['get', 'sidoCode'], sido],
      0.9,
      0.72,
    ]);
  }, [hovered, viewSido]);

  // 시도 뷰 순위 배지(1·2·3)는 뺐습니다 — 시군구 이름 라벨과 겹쳐 소음이었고,
  // 순위는 우측 목록과 등급색으로 이미 읽힙니다.

  useEffect(() => {
    viewSidoRef.current = viewSido;
    const map = mapRef.current;
    if (map) applyLevel(map, viewSido);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewSido]);

  useEffect(() => {
    selectedCodeRef.current = selectedCode;
    const map = mapRef.current;
    if (!map || !mapReadyRef.current) return;
    map.setFilter('selected-line', [
      '==',
      ['get', 'code'],
      selectedCode ?? '__none__',
    ]);
    // 선택하면 나머지 시군구는 회색으로. 해제하면 시도 등급색 복원.
    if (viewSidoRef.current) {
      applyDistrictFill(map, viewSidoRef.current, selectedCode);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedCode]);

  // 동 뷰 진입/이탈: 동 레이어 필터와 카메라, 시군구 채움 투명도.
  useEffect(() => {
    dongMapCodeRef.current = dongMapCode;
    // 진입·이탈 모두 직전 동 선택은 무효입니다.
    setSelectedDong(null);
    const map = mapRef.current;
    if (!map || !mapReadyRef.current) return;

    const target = dongMapCode ?? '__none__';
    map.setFilter('dongs-fill', ['==', ['get', 'sgg_code'], target]);
    map.setFilter('dongs-line', ['==', ['get', 'sgg_code'], target]);

    // 동 이름을 우리가 그리는 동안에는 브이월드를 걷어냅니다. 타일에 박힌
    // 동 이름과 우리 라벨이 같은 자리에서 두 번 겹쳐 읽을 수 없게 됩니다.
    if (map.getLayer('basemap-detail')) {
      map.setPaintProperty(
        'basemap-detail',
        'raster-opacity',
        dongMapCode ? 0 : DETAIL_OPACITY_BY_ZOOM,
      );
    }

    if (dongMapCode) {
      // 해당 시군구 채움은 거의 걷어내 배경지도와 동 경계가 읽히게 합니다.
      map.setPaintProperty('districts-fill', 'fill-opacity', [
        'case',
        ['==', ['get', 'code'], dongMapCode],
        0.06,
        ['==', ['get', 'sidoCode'], viewSidoRef.current ?? '__none__'],
        0.35,
        0.15,
      ]);
      // 들어와 있는 시군구의 이름표는 치웁니다 — 동 폴리곤 한가운데 얹혀서
      // 동 이름과 겹칩니다. 이웃 시군구 이름은 방위 참고로 남깁니다.
      map.setFilter('sgg-labels', [
        'all',
        ['==', ['get', 'sidoCode'], viewSidoRef.current ?? '__none__'],
        ['!=', ['get', 'code'], dongMapCode],
      ]);
      const bounds = enrichedRef.current?.districtBounds.get(dongMapCode);
      // maxZoom 을 두는 이유: 서울 중구처럼 작은 시군구는 꽉 맞추면 z14 까지 들어가
      // 주변 맥락이 사라지고, 옆 시군구를 바로 누를 수 없습니다. 동 이름은 z12 면
      // 충분히 읽힙니다.
      if (bounds) map.fitBounds(bounds, { padding: 56, maxZoom: 12, duration: 700 });
    } else if (viewSidoRef.current) {
      applyLevel(map, viewSidoRef.current);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dongMapCode]);

  // 동 이름: 지금 줌에서 이름이 그 동 안에 들어가는 것만 그립니다. 좁은 동에
  // 라벨을 얹으면 글씨가 옆 동까지 넘어가 어느 구역 이름인지 알 수 없습니다.
  // 줌마다 답이 달라지므로 이동이 끝날 때마다 다시 판정합니다.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReadyRef.current) return;
    const source = map.getSource('dong-labels') as
      | maplibregl.GeoJSONSource
      | undefined;
    if (!source) return;

    const features = dongMapCode ? dongCacheRef.current.get(dongMapCode) : undefined;
    if (!features) {
      dongLabelBoxRef.current = [];
      source.setData(EMPTY_FC);
      map.setFilter('dong-labels', ['==', ['get', 'adm_code'], '__none__']);
      return;
    }
    const { labels, boxes } = dongLabels(features);
    dongLabelBoxRef.current = boxes;
    source.setData(labels);

    const apply = () => {
      // ① 이름이 그 동 안에 들어가는지 (앵커 자리의 실제 내부 여유로 판정)
      const candidates: Array<{
        code: string;
        room: number;
        rect: [number, number, number, number];
      }> = [];
      for (const { code, name, spanLon, spanLat, anchor } of dongLabelBoxRef.current) {
        const left = map.project([spanLon[0], anchor[1]]);
        const right = map.project([spanLon[1], anchor[1]]);
        const bottom = map.project([anchor[0], spanLat[0]]);
        const top = map.project([anchor[0], spanLat[1]]);
        const room = Math.abs(right.x - left.x);
        const height = Math.abs(bottom.y - top.y);
        // 한글은 글자당 폭이 대략 글자 크기와 같습니다. 좌우 여백 8px.
        const textWidth = name.length * DONG_LABEL_SIZE;
        if (room < textWidth + 8 || height < DONG_LABEL_SIZE * 1.8) continue;
        const center = map.project(anchor);
        candidates.push({
          code,
          room,
          rect: [
            center.x - textWidth / 2 - LABEL_GAP,
            center.y - DONG_LABEL_SIZE / 2 - LABEL_GAP,
            center.x + textWidth / 2 + LABEL_GAP,
            center.y + DONG_LABEL_SIZE / 2 + LABEL_GAP,
          ],
        });
      }

      // ② 화면에서 서로 부딪히는 이름 걸러내기. 넓은 동을 먼저 앉히고, 겹치는
      // 이름은 버립니다 — 좁은 동이 몰린 곳에서 글씨가 뒤엉키는 것을 막습니다.
      // maplibre 자체 충돌 회피는 글자끼리만 보므로 여백까지 두지는 못합니다.
      candidates.sort((a, b) => b.room - a.room);
      const placed: Array<[number, number, number, number]> = [];
      const fitting: string[] = [];
      for (const { code, rect } of candidates) {
        const clash = placed.some(
          (other) =>
            rect[0] < other[2] &&
            rect[2] > other[0] &&
            rect[1] < other[3] &&
            rect[3] > other[1],
        );
        if (clash) continue;
        placed.push(rect);
        fitting.push(code);
      }
      map.setFilter('dong-labels', [
        'in',
        ['get', 'adm_code'],
        ['literal', fitting],
      ]);
    };

    apply();
    map.on('moveend', apply);
    return () => {
      map.off('moveend', apply);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dongMapCode, dongVersion]);

  // 동 선택 하이라이트 — 목록 클릭과 지도 클릭이 같은 상태를 씁니다.
  useEffect(() => {
    selectedDongRef.current = selectedDong;
    const map = mapRef.current;
    if (!map || !mapReadyRef.current) return;
    const target = selectedDong ?? '__none__';
    map.setFilter('dong-selected-fill', ['==', ['get', 'adm_code'], target]);
    map.setFilter('dong-selected-line', ['==', ['get', 'adm_code'], target]);
  }, [selectedDong]);

  // 강수 모드가 바뀌면 시군구 채움만 다시 칠합니다 (전국 뷰는 시도색이라 무관).
  useEffect(() => {
    rainModeRef.current = rainMode;
    const map = mapRef.current;
    if (!map || !mapReadyRef.current || !viewSidoRef.current) return;
    applyDistrictFill(map, viewSidoRef.current, selectedCodeRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rainMode]);

  const rankedDistricts = useMemo(() => {
    const rows = list?.districts ?? [];
    const filtered = viewSido
      ? rows.filter((row) => row.sido_code === viewSido)
      : rows;
    if (rainMode && viewSido) {
      return [...filtered].sort(
        (a, b) =>
          (triggeredRankByCode.get(a.sgg_code) ?? Infinity) -
          (triggeredRankByCode.get(b.sgg_code) ?? Infinity),
      );
    }
    return [...filtered].sort((a, b) => a.rank - b.rank);
  }, [list, viewSido, rainMode, triggeredRankByCode]);

  // 강수 모드에서 상세 상단 요약 카드(전국 순위·시도 내·전국 상위)를 강수 반영
  // 값으로 바꿔 끼우기 위한 계산. 공식 값과 같은 형태라 화면 구조는 그대로입니다.
  const detailTriggeredStats = useMemo(() => {
    if (!detail || !list) return null;
    const rank = triggeredRankByCode.get(detail.sgg_code);
    if (rank == null) return null;
    const sido = detail.sgg_code.slice(0, 2);
    const sidoRanks = list.districts
      .filter(
        (row) =>
          row.sido_code === sido && triggeredRankByCode.has(row.sgg_code),
      )
      .map((row) => triggeredRankByCode.get(row.sgg_code) as number)
      .sort((a, b) => a - b);
    return {
      rank,
      total: triggeredRankByCode.size,
      sidoRank: sidoRanks.indexOf(rank) + 1 || null,
      sidoCount: sidoRanks.length,
    };
  }, [detail, list, triggeredRankByCode]);

  const currentSido = viewSido ? sidoByCode.get(viewSido) : undefined;
  const hoveredScore = hovered ? scoreByCode.get(hovered) : undefined;
  const hoveredSido = hovered ? sidoByCode.get(hovered.slice(0, 2)) : undefined;
  const dongDistrictName = dongSgg
    ? (scoreByCode.get(dongSgg)?.full_name ?? dongInfo?.full_name ?? '')
    : '';
  const hoveredDongData =
    hoveredDong && dongInfo
      ? dongInfo.dongs.find((dong) => dong.adm_code === hoveredDong)
      : undefined;
  const selectedDongData =
    selectedDong && dongInfo
      ? dongInfo.dongs.find((dong) => dong.adm_code === selectedDong)
      : undefined;
  const hoveredName = useMemo(() => {
    if (!hovered || !geojson) return null;
    const feature = geojson.features.find((f) => f.properties.code === hovered);
    return feature ? `${feature.properties.sido_name} ${feature.properties.name}` : null;
  }, [hovered, geojson]);

  const drillInto = (sidoCode: string) => {
    setViewSido(sidoCode);
    setSelectedCode(null);
    setDongSgg(null);
    setHovered(null);
    setTooltip(null);
  };

  const backToNation = () => {
    setViewSido(null);
    setSelectedCode(null);
    setDongSgg(null);
    setHovered(null);
    setTooltip(null);
  };

  const backToSido = () => {
    setDongSgg(null);
    // 동 뷰 이탈이므로 직전 동 선택도 무효 — dongMapCode 가 유지돼 자동으로
    // 비워지지 않습니다.
    setSelectedDong(null);
    setHoveredDong(null);
    setTooltip(null);
    // 동으로 확대한 카메라를 시군구 화면으로 되돌립니다. dongMapCode 가
    // selectedCode 로 그대로 이어져 [dongMapCode] 효과가 다시 돌지 않기 때문에
    // 여기서 직접 맞춥니다.
    const map = mapRef.current;
    const code = selectedCodeRef.current;
    const bounds = code ? enrichedRef.current?.districtBounds.get(code) : undefined;
    if (map && bounds) map.fitBounds(bounds, { padding: 56, maxZoom: 12, duration: 700 });
  };

  // 동 목록에서 클릭: 그 동을 선택(하이라이트)하고 경계에 맞춰 다가갑니다.
  // 좌표는 이미 받아 둔 동 경계 GeoJSON 에서 꺼냅니다 — 별도 중심점 데이터가
  // 필요 없고, 중심점과 달리 길쭉한 동도 화면에 온전히 들어옵니다.
  const focusDong = (dong: AdminDong) => {
    setSelectedDong((prev) => (prev === dong.adm_code ? null : dong.adm_code));
    const map = mapRef.current;
    if (!map || !dongMapCode) return;
    const feature = dongCacheRef.current
      .get(dongMapCode)
      ?.find((item) => item.properties.adm_code === dong.adm_code);
    if (!feature) return;
    const rings: number[][][] =
      feature.geometry.type === 'Polygon'
        ? (feature.geometry.coordinates as number[][][])
        : (feature.geometry.coordinates as number[][][][]).flat();
    let minLon = Infinity;
    let minLat = Infinity;
    let maxLon = -Infinity;
    let maxLat = -Infinity;
    for (const ring of rings) {
      for (const [lon, lat] of ring) {
        if (lon < minLon) minLon = lon;
        if (lat < minLat) minLat = lat;
        if (lon > maxLon) maxLon = lon;
        if (lat > maxLat) maxLat = lat;
      }
    }
    if (minLon > maxLon) return;
    map.fitBounds(
      [
        [minLon, minLat],
        [maxLon, maxLat],
      ],
      { padding: 96, duration: 600, maxZoom: 13.5 },
    );
  };

  const meta = list?.meta ?? null;
  const grades = meta?.grades ?? {};
  const gradeLegend = Object.keys(grades)
    .map((grade) => ({
      grade: Number(grade),
      label: grades[grade].label,
    }))
    .sort((a, b) => b.grade - a.grade);

  return (
    // section 입니다. 셸이 이미 <main> 을 두고 있어서 여기서 또 열면
    // 문서에 main 이 두 개가 됩니다.
    <section className="priority-page-shell flex-1 p-6 w-full">
      {/* 상단: 제목 + 선택 동 검사 시작 */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <h2 className="text-lg font-bold text-[#1a1b20] flex items-center gap-2">
          <MapIcon className="w-5 h-5 text-[#0f458f]" />
          시군구별 점검 우선순위
        </h2>
        <button
          type="button"
          onClick={() => onStartInspection(
            selectedDongData?.adm_name ?? null,
            selectedDongData
              ? scoreByCode.get(selectedDongData.sgg_code)?.district_id
              : undefined,
          )}
          className="flex items-center gap-1.5 px-3.5 py-2 text-sm font-semibold text-white bg-[#0f458f] rounded-lg shadow-sm hover:bg-[#0c3872] transition-colors cursor-pointer"
        >
          <Play className="w-4 h-4" fill="currentColor" />
          점검 시작
        </button>
      </div>

      {loadError && (
        <div className="mb-4 px-4 py-3 text-sm text-[#d03b3b] bg-[#fbe9e9] border border-[#f2c9c9] rounded-lg">
          <div className="flex items-center justify-between gap-3">
            <span>{loadError}</span>
            {/* 재시도 경로가 없으면 유일한 복구가 탭 이탈 후 재진입입니다. */}
            <button
              onClick={() => setReloadKey((prev) => prev + 1)}
              className="shrink-0 px-3 py-1 text-xs font-semibold text-[#d03b3b] border border-[#f2c9c9] rounded-md hover:bg-[#f7dcdc] cursor-pointer"
            >
              다시 시도
            </button>
          </div>
          {/* 개발 서버 프록시는 대상이 연결을 거부하면 502 가 아니라 500 을
              돌려줍니다. 그래서 500 은 대개 "백엔드 에러"가 아니라
              "백엔드가 안 떠 있음"입니다. 그 구분을 문구에 넣습니다. */}
          <div className="mt-1 text-[#a13333]">
            500 이면 대개 백엔드가 안 떠 있는 것입니다 — 127.0.0.1:8001 의
            cloud_api 와 MongoDB 를 확인하세요.
          </div>
        </div>
      )}

      {/* 드릴다운 경로 */}
      <div className="flex items-center gap-1 mb-3 text-sm">
        <button
          onClick={backToNation}
          disabled={!viewSido}
          aria-current={viewSido ? undefined : 'page'}
          className={`px-2 py-1 rounded-md cursor-pointer transition-colors ${
            viewSido
              ? 'font-medium text-[#0f458f] hover:bg-[#eef3fd]'
              : 'font-bold text-[#1a1b20] cursor-default'
          }`}
        >
          전국
        </button>
        {currentSido && (
          <>
            <ChevronRight className="w-4 h-4 text-[#c3c2b7]" />
            <button
              onClick={backToSido}
              disabled={!dongSgg}
              aria-current={dongSgg ? undefined : 'page'}
              className={`px-2 py-1 rounded-md transition-colors ${
                dongSgg
                  ? 'font-medium text-[#0f458f] hover:bg-[#eef3fd] cursor-pointer'
                  : 'font-bold text-[#1a1b20] cursor-default'
              }`}
            >
              {currentSido.sido_name}
            </button>
            {!dongSgg && (
              // -top-px: 폰트 메트릭 때문에 작은 글씨가 굵은 이름보다 낮게
              // 렌더링됩니다. 스크린샷으로 맞춘 광학 보정입니다.
              <span className="text-xs text-[#737782]">
                시도 {currentSido.rank}위 · 시군구 {currentSido.district_count}개
              </span>
            )}
            {/* 강수 반영 보기 — 시군구 레벨에서만. 공식 순위는 그대로이므로 켠
                동안에는 참고용 띠를 함께 노출합니다. */}
            {!dongSgg && (
              <span className="ml-2 flex items-center gap-2">
                <button
                  onClick={() => setRainMode((prev) => !prev)}
                  aria-pressed={rainMode}
                  title="최근 30일 강수까지 반영한 순위로 봅니다. 강수는 시점에 따라 변해 공식 순위·등급에는 반영하지 않습니다."
                  className={`flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-semibold rounded-lg border transition-colors cursor-pointer ${
                    rainMode
                      ? 'bg-[#0f458f] border-[#0f458f] text-white'
                      : 'bg-[#e3eefc] border-[#b7d0f2] text-[#0f458f] hover:bg-[#d3e4fa]'
                  }`}
                >
                  <CloudRain className="w-3.5 h-3.5" />
                  강수 반영
                </button>
                {rainMode && (
                  <span className="text-[11px] font-medium text-[#0f458f]">
                    최근 30일 강수 반영
                  </span>
                )}
              </span>
            )}
          </>
        )}
        {dongSgg && (
          <>
            <ChevronRight className="w-4 h-4 text-[#c3c2b7]" />
            <span className="px-2 py-1 font-bold text-[#1a1b20]">
              {dongDistrictName.split(' ').pop()}
            </span>
            {/* "점수 미산정"은 지도 좌하단 범례가 이미 말합니다 — 경로 줄에서
                한 번 더 말하면 이름보다 주석이 길어져 경로가 읽히지 않습니다. */}
            <span className="text-xs text-[#737782]">
              읍면동 {dongInfo?.total ?? 0}개
            </span>
          </>
        )}
        {!viewSido && (
          <span className="ml-1 text-xs text-[#898781]">
            시도를 클릭하면 시군구 단위로 들어갑니다
          </span>
        )}
        {(detailError || dongError) && (
          <span role="alert" className="ml-2 text-xs text-[#d03b3b]">
            {detailError ?? dongError}
          </span>
        )}
      </div>

      {/* items-start 를 쓰면 격자 항목이 늘어나지 않아 남는 높이를 못 채웁니다.
          두 카드가 같은 높이로 늘어나야 페이지 스크롤이 사라집니다. */}
      <div className="priority-layout grid gap-4">
        {/* 지도 */}
        <div
          ref={mapBoxRef}
          className="priority-map-card relative bg-white border border-[#e2e2e9] rounded-xl shadow-sm overflow-hidden"
        >
          <div ref={mapContainerRef} className="priority-map-frame w-full" />
          {!enriched && !loadError && (
            <div className="absolute inset-0 flex items-center justify-center bg-white text-sm text-[#737782]">
              경계·순위 데이터를 불러오는 중… (1.3 MB, 최초 1회)
            </div>
          )}

          {/* 호버 툴팁 */}
          {(hovered || hoveredDong) && tooltip && (
            <div
              className="absolute pointer-events-none z-10 px-3 py-2 bg-white border border-[#e2e2e9] rounded-lg shadow-md text-xs"
              style={{
                left: Math.min(tooltip.x + 14, (mapBoxRef.current?.clientWidth ?? 400) - 200),
                top: tooltip.y + 14,
                width: 188,
              }}
            >
              {dongSgg && hoveredDongData ? (
                <>
                  <p className="font-bold text-[#1a1b20]">
                    {hoveredDongData.adm_name.split(' ').pop()}
                  </p>
                  <div className="mt-1 space-y-0.5 text-[#52514e]">
                    <p>
                      지반침하 이력{' '}
                      <span className="font-semibold">
                        {hoveredDongData.subsidence_count}건
                      </span>
                      {hoveredDongData.sewer_cause_count > 0 && (
                        <> (하수관 원인 {hoveredDongData.sewer_cause_count})</>
                      )}
                    </p>
                  </div>
                </>
              ) : !viewSido && hoveredSido ? (
                <>
                  <p className="font-bold text-[#1a1b20]">{hoveredSido.sido_name}</p>
                  <div className="mt-1 space-y-0.5 text-[#52514e]">
                    <p>
                      시도 <span className="font-semibold">{hoveredSido.rank}위</span> ·
                      가중 위험도 {hoveredSido.weighted_score.toFixed(3)}
                    </p>
                    <p>
                      최우선 {hoveredSido.grade_counts['5'] ?? 0}곳 /{' '}
                      {hoveredSido.district_count}개 시군구
                    </p>
                    {hoveredSido.top_district && (
                      <p className="text-[#898781]">1위: {hoveredSido.top_district}</p>
                    )}
                  </div>
                </>
              ) : (
                <>
                  <p className="font-bold text-[#1a1b20]">{hoveredName}</p>
                  {hoveredScore ? (
                    // 강수 모드에서는 지도 색과 같은 값(triggered 순위·접힌 등급)을
                    // 보여줍니다 — 색은 강수 반영인데 글자만 평시면 모순입니다.
                    rainMode && triggeredRankByCode.has(hoveredScore.sgg_code) ? (
                      <div className="mt-1 space-y-0.5 text-[#52514e]">
                        <p>
                          전국{' '}
                          <span className="font-semibold">
                            {triggeredRankByCode.get(hoveredScore.sgg_code)}위
                          </span>
                        </p>
                        <p>
                          강수 반영 {(hoveredScore.triggered_score ?? hoveredScore.score).toFixed(3)} ·{' '}
                          <span className="font-semibold">
                            {GRADE_LABELS[
                              foldRankToGrade(
                                triggeredRankByCode.get(hoveredScore.sgg_code)!,
                                triggeredRankByCode.size,
                              )
                            ]}
                          </span>
                        </p>
                      </div>
                    ) : (
                      <div className="mt-1 space-y-0.5 text-[#52514e]">
                        <p>
                          전국 <span className="font-semibold">{hoveredScore.rank}위</span>
                          {hoveredScore.sido_rank != null && (
                            <>
                              {' '}
                              · 시도 내 {hoveredScore.sido_rank}/{hoveredScore.sido_count}위
                            </>
                          )}
                        </p>
                        <p>
                          위험도 {hoveredScore.score.toFixed(3)} ·{' '}
                          <span className="font-semibold">{hoveredScore.grade_label}</span>
                        </p>
                      </div>
                    )
                  ) : (
                    <p className="mt-1 text-[#898781]">산정 결과 없음</p>
                  )}
                </>
              )}
            </div>
          )}

          {/* 범례 — 바깥은 블록, 안쪽만 flex 입니다. flex-wrap 컨테이너의 고유
              너비는 줄바꿈을 무시하고 모든 항목을 한 줄로 합산해서 나오므로,
              한 상자에 담으면 아래 줄(기준일) 폭이 오른쪽 빈칸으로 남습니다. */}
          <div className="absolute left-3 bottom-3 z-10 w-fit px-3 py-2 bg-white/92 border border-[#e2e2e9] rounded-lg shadow-sm">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            {dongSgg ? (
              <div className="flex items-center gap-1.5">
                <span
                  className="inline-block w-3 h-3 rounded-[3px]"
                  style={{ backgroundColor: DONG_FILL, opacity: 0.45 }}
                />
                <span className="text-[11px] text-[#52514e]">
                  읍면동 경계 · 침하 이력 표시
                </span>
              </div>
            ) : viewSido
              ? gradeLegend.map((band) => (
                  <div key={band.grade} className="flex items-center gap-1.5">
                    <span
                      className="inline-block w-3 h-3 rounded-[3px]"
                      style={{ backgroundColor: GRADE_COLORS[band.grade] }}
                    />
                    <span className="text-[11px] text-[#52514e]">
                      {band.grade} {band.label}
                    </span>
                  </div>
                ))
              : [5, 4, 3, 2, 1].map((step) => (
                  <div key={step} className="flex items-center gap-1">
                    <span
                      className="inline-block w-3 h-3 rounded-[3px]"
                      style={{ backgroundColor: GRADE_COLORS[step] }}
                    />
                    {step === 5 && (
                      <span className="text-[11px] text-[#52514e]">높음</span>
                    )}
                    {step === 1 && (
                      <span className="text-[11px] text-[#52514e]">낮음</span>
                    )}
                  </div>
                ))}
            </div>
            {/* 행정구역을 의도적으로 고정했다는 표시. 원천 통계가 전부 개편 전
                기준이라 경계만 최신으로 올리면 조인이 조용히 깨집니다.
                긴 설명은 title 로 — 문구가 길면 범례 상자가 제주를 덮습니다. */}
            <div
              className="mt-1 text-[10px] text-[#a5a3a0] whitespace-nowrap"
              title="하수도통계(2024)·주민등록인구 등 원천 통계가 모두 2026-07-01 행정구역 개편 전 기준이라 경계도 같은 기준으로 고정했습니다."
            >
              행정구역 2026-06-30 기준
            </div>
          </div>
        </div>

        {/* 우측 패널 */}
        <div className="priority-side-panel bg-white border border-[#e2e2e9] rounded-xl shadow-sm overflow-hidden">
          {dongSgg ? (
            <DongListPanel
              districtName={dongDistrictName}
              info={dongInfo}
              selectedCode={selectedDong}
              onBack={backToSido}
              onFocus={focusDong}
            />
          ) : detail || detailLoading ? (
            <DetailPanel
              detail={detail}
              loading={detailLoading}
              onClose={() => setSelectedCode(null)}
              dongTotal={dongInfo?.total ?? 0}
              onShowDongs={() => detail && setDongSgg(detail.sgg_code)}
              history={history}
              rainMode={rainMode}
              triggeredStats={detailTriggeredStats}
              triggeredRank={
                detail ? triggeredRankByCode.get(detail.sgg_code) : undefined
              }
            />
          ) : viewSido ? (
            <DistrictListPanel
              title={
                rainMode
                  ? `${currentSido?.sido_name ?? ''} 강수 반영 순위`
                  : `${currentSido?.sido_name ?? ''} 시군구 순위`
              }
              rows={rankedDistricts}
              rainMode={rainMode}
              triggeredRanks={triggeredRankByCode}
              onSelect={(code) => setSelectedCode(code)}
              onBack={backToNation}
            />
          ) : (
            <SidoListPanel rows={sidoList} onSelect={drillInto} />
          )}
        </div>
      </div>
    </section>
  );
}
