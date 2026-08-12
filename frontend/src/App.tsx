import { type MouseEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as GaussianSplats3D from '@mkkellogg/gaussian-splats-3d';
import PriorityMap from './components/PriorityMap';
import { useSessionState } from './hooks/useSessionState';
import { sectionOptionsFor } from './data/inspectionSections';
import {
  explicitRunningInspectionIdentity,
  inspectionIdentityKey,
  motorOutputPercent,
  shouldAcceptAutonomousCompletion,
} from './services/agent.mjs';
import {
  Activity, ArrowLeft, ArrowRight, Bot,
  Box, CalendarDays, CircleCheck, CircleStop, Clock3, FileBarChart, Home, LayoutDashboard, MapPin,
  Menu, Play, ShieldCheck,
  SlidersHorizontal, Wifi, X,
} from 'lucide-react';

type Page = 'dashboard' | 'monitoring' | 'report' | 'threeD' | 'priority';
type RobotState = 'ready' | 'running' | 'paused' | 'returning' | 'emergency';
type Severity = 'Critical' | 'Warning' | 'Normal';

interface Defect {
  id: string;
  type: string;
  severity: Severity;
  distance: string;
  time: string;
  // 화면에 쓰는 `distance`·`time` 은 사람이 읽을 문자열이라 계산에 못 쓴다.
  // 검사 시간 타임라인이 결함을 축 위에 찍으려면 원본 값이 필요해 함께 남긴다.
  detectedAtMs?: number;
  positionM?: number;
  confidence?: number;
  // 사진 판독이 끝났는가. 결함 통계는 이것이 참인 것만 센다 — 판독 전에 세면
  // 나중에 반려될 때 결함 수가 줄어들어 계산이 틀린 것처럼 보인다.
  photoReviewed?: boolean;
  status: string;
  image: string;
}

interface LiveData {
  speed?: number;
  tilt?: number;
  roll?: number;
  pitch?: number;
  yaw?: number;
  distance?: number;
  temperature?: number;
  latency?: number;
  voltage?: number;
  operatingMinutes?: number;
  missionState?: string;
  section?: string;
  detectionConfidence?: number;
  inspectionProgress?: number;
}

interface RobotEvent {
  id: string;
  time: string;
  category: string;
  message: string;
  level: string;
}

interface AiSummaryData {
  message: string;
  time: string;
  model: string;
  tone: 'idle' | 'pending' | 'ready' | 'error';
}

// 요약이 이보다 오래되면 지금 상황이 아니라고 본다. Agent 는 점검 중 수십 초마다
// 새 요약을 내므로 3분이면 넉넉하고, 지난 점검 것이 넘어오는 것은 확실히 걸러낸다.
const SUMMARY_STALE_AFTER_MS = 180_000;

const idleAiSummary = (): AiSummaryData => ({
  message: 'AI 요약을 기다리고 있습니다.',
  time: '--:--:--',
  model: 'Qwen2.5-VL',
  tone: 'idle',
});

interface CloudSession {
  id: string | number;
  area?: string;
  region?: string;
  status?: string;
  started_at?: string;
  ended_at?: string;
  robot_id?: string | number;
  approved_by?: string | number;
  district_id?: string | number;
  mode?: string;
  direction?: string;
  inspection_direction?: string;
  start_point?: string;
  startPoint?: string;
  manhole?: string;
  // 점검 시작 팝업에서 고른 값이 여기 실려 온다. 점검을 시작하지 않은 화면도
  // 이걸 읽어야 같은 구간·방향을 보여 줄 수 있다.
  survey?: {
    survey_location?: string;
    inspection_section?: string;
    inspection_direction?: string;
  };
  defect_summary?: {
    total?: number;
    critical?: number;
    warning?: number;
    normal?: number;
  };
}

interface CloudReport {
  id: string | number;
  report_no?: string;
  session_id: string | number;
  region?: string;
  survey_location?: string;
  robot_name?: string;
  robot_model?: string;
  operator_name?: string;
  operator_role?: string;
  inspection_date?: string;
  duration?: string;
  total_defect_count?: number;
  critical_defect_count?: number;
  summary?: string;
  generated_at?: string;
}

interface CloudRobot {
  id: string | number;
  name?: string;
  model?: string;
  status?: string;
}

interface CloudOperator {
  id: string | number;
  name?: string;
  role?: string;
}

interface InspectionSetupValues {
  /** 지도에서 고른 동. 「광주광역시 광산구 하남동」처럼 시도까지 붙는다. */
  sectionName: string;
  /** 그 동 안에서 실제로 점검할 구간. 「... 하남동 구간2」 */
  section: string;
  robotId: string;
  operatorId: string;
  /** 승인 운영자 확인 코드. 시연 중 남이 임의로 점검을 걸지 못하게 막는다. */
  operatorCode: string;
  direction: string;
  configuredAt: string;
  districtId?: number;
  sessionId?: string | number;
  operationId?: string;
}

const API_BASE_URL = String(import.meta.env.VITE_API_BASE_URL ?? '/api').replace(/\/$/, '');
const API_ORIGIN = String(import.meta.env.VITE_API_ORIGIN ?? window.location.origin).replace(/\/$/, '');
const AGENT_API_URL = String(import.meta.env.VITE_AGENT_API_URL ?? 'http://127.0.0.1:8000').replace(/\/$/, '');

const createInspectionSetupValues = (): InspectionSetupValues => ({
  sectionName: '',
  section: '',
  robotId: '',
  operatorId: '',
  operatorCode: '',
  direction: '',
  configuredAt: new Date().toISOString(),
});

// 로봇을 실제로 쓸 수 있는 상태. 대장에 여러 대가 있어도 지금 굴러가는 건 READY 뿐이다.
const ROBOT_READY_STATUS = 'READY';

const inspectionDirectionLabel = (direction?: string) => {
  if (!direction) return undefined;
  const normalized = direction.toUpperCase();
  if (normalized === 'FORWARD') return '정방향 (→)';
  if (normalized === 'REVERSE') return '역방향 (←)';
  if (normalized === 'BOTH') return '양방향 (↔)';
  return direction;
};

// 시각 문자열은 두 경로로 들어온다.
//
//   팝업이 넘긴 configuredAt  "2026-08-09T07:55:33.000Z"  ← Z 있음
//   API 의 session.started_at "2026-08-09T07:55:33"       ← Z 없음. 값은 UTC
//
// `new Date()` 는 표준에 따라 **시간대 표기가 없으면 로컬시각으로** 읽는다. 그래서
// 뒤엣것이 07:55 KST 로 잡혀 화면에 9시간 이른 시각이 나왔다(16:55 → 7시 55분).
// `parseUtcTimestamp` 가 없는 Z 를 붙여 주므로 두 경로가 같은 값이 된다.
const inspectionDateTimeLabel = (value?: string) => {
  if (!value) return undefined;
  const milliseconds = parseUtcTimestamp(value);
  return Number.isNaN(milliseconds) ? value : new Date(milliseconds).toLocaleString('ko-KR', { hour12: false });
};

const inspectionElapsedLabel = (startedAt?: string, endedAt?: string) => {
  if (!startedAt) return undefined;
  // 여기서 로컬로 잘못 읽으면 경과가 9시간 부풀어 「09:00:21」 같은 값이 나온다.
  const started = parseUtcTimestamp(startedAt);
  if (!Number.isFinite(started)) return undefined;
  const ended = endedAt ? parseUtcTimestamp(endedAt) : Date.now();
  if (!Number.isFinite(ended)) return undefined;
  const elapsedSeconds = Math.max(0, Math.floor((ended - started) / 1000));
  const hours = Math.floor(elapsedSeconds / 3600);
  const minutes = Math.floor((elapsedSeconds % 3600) / 60);
  const seconds = elapsedSeconds % 60;
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
};

const numericValue = (...values: unknown[]) => {
  const value = values.find((candidate) => typeof candidate === 'number' && Number.isFinite(candidate));
  return typeof value === 'number' ? value : undefined;
};

const textValue = (...values: unknown[]) => {
  const value = values.find((candidate) => typeof candidate === 'string' && candidate.trim());
  return typeof value === 'string' ? value : undefined;
};

const parseUtcTimestamp = (value?: string) => {
  if (!value) return Number.NaN;
  const hasTimezone = /[zZ]$|[+-]\d{2}:\d{2}$/.test(value);
  return Date.parse(hasTimezone ? value : `${value}Z`);
};

const asRecord = (value: unknown): Record<string, any> =>
  value && typeof value === 'object' ? value as Record<string, any> : {};

const asArray = (value: unknown): Array<Record<string, any>> => {
  if (Array.isArray(value)) return value.map(asRecord);
  const record = asRecord(value);
  const candidate = record.content ?? record.items ?? record.data ?? record.results;
  return Array.isArray(candidate) ? candidate.map(asRecord) : [];
};

const isInteractiveTarget = (target: EventTarget | null) =>
  target instanceof HTMLElement && Boolean(target.closest('button, a, input, select, textarea'));

function useDragScroll<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const draggingRef = useRef(false);
  const wasDraggedRef = useRef(false);
  const startRef = useRef({ x: 0, y: 0, left: 0, top: 0 });

  const onMouseDown = useCallback((event: MouseEvent<T>) => {
    if (event.button !== 0 || isInteractiveTarget(event.target)) return;
    const element = ref.current;
    if (!element) return;
    draggingRef.current = true;
    wasDraggedRef.current = false;
    startRef.current = {
      x: event.clientX,
      y: event.clientY,
      left: element.scrollLeft,
      top: element.scrollTop,
    };
    element.classList.add('is-dragging');
  }, []);

  const stopDragging = useCallback(() => {
    draggingRef.current = false;
    ref.current?.classList.remove('is-dragging');
  }, []);

  const onMouseMove = useCallback((event: MouseEvent<T>) => {
    if (!draggingRef.current) return;
    const element = ref.current;
    if (!element) return;
    const deltaX = event.clientX - startRef.current.x;
    const deltaY = event.clientY - startRef.current.y;
    if (Math.abs(deltaX) > 3 || Math.abs(deltaY) > 3) wasDraggedRef.current = true;
    event.preventDefault();
    element.scrollLeft = startRef.current.left - deltaX;
    element.scrollTop = startRef.current.top - deltaY;
  }, []);

  const onClickCapture = useCallback((event: MouseEvent<T>) => {
    if (!wasDraggedRef.current) return;
    event.preventDefault();
    event.stopPropagation();
    wasDraggedRef.current = false;
  }, []);

  return {
    ref,
    onMouseDown,
    onMouseMove,
    onMouseUp: stopDragging,
    onMouseLeave: stopDragging,
    onClickCapture,
  };
}

const normalizeSeverity = (value: unknown): Severity => {
  const normalized = String(value ?? '').toLowerCase();
  if (normalized.includes('critical') || normalized.includes('high') || normalized === '1') return 'Critical';
  if (normalized.includes('warning') || normalized.includes('medium') || normalized === '2') return 'Warning';
  return 'Normal';
};

const formatNumber = (value: number | undefined, digits = 1) =>
  value === undefined ? '--' : value.toFixed(digits);

const toPercent = (value: number | undefined) => {
  if (value === undefined) return undefined;
  return value <= 1 ? value * 100 : value;
};

const navItems: Array<{ id: Page; label: string; icon: typeof LayoutDashboard }> = [
  { id: 'priority', label: '우선순위', icon: MapPin },
  { id: 'dashboard', label: '대시보드', icon: LayoutDashboard },
  { id: 'monitoring', label: '모니터링', icon: Activity },
  { id: 'threeD', label: '3D', icon: Box },
  { id: 'report', label: '보고서', icon: FileBarChart },
];

const severityStyle: Record<Severity, string> = {
  Critical: 'badge badge-critical',
  Warning: 'badge badge-warning',
  Normal: 'badge badge-normal',
};

// 「요약 재생성」을 열어 둘지. 시연장에서는 잠근다 — 누구나 누를 수 있는 자리에
// 있는데 한 번 누를 때마다 LLM 호출이 나가고 수십 초가 걸려, 여러 사람이 연달아
// 누르면 게이트웨이가 밀려 정작 필요한 보고서 생성이 늦어진다.
//
// 시연이 끝나면 이 값만 true 로 되돌리면 버튼이 살아난다. 재생성 로직 자체는
// 그대로 두었으므로 지우거나 되살릴 코드가 없다.
const SUMMARY_REGENERATION_ENABLED = false;

// 요약 재생성·PDF 렌더링은 LLM 게이트웨이를 타서 길면 1분이 넘는다. 그래도 상한이
// 없으면 게이트웨이가 응답을 끊었을 때 요청이 영영 매달려 버튼이 잠긴 채로 남는다.
// 3분은 관측된 최장(사진 5장 PDF 약 58초)의 세 배로, 정상 작업을 자르지 않는다.
const REPORT_JOB_TIMEOUT_MS = 180_000;

const reportJobErrorMessage = (error: unknown, job: string) => {
  if (error instanceof DOMException && error.name === 'TimeoutError') {
    return `${job}이 ${REPORT_JOB_TIMEOUT_MS / 1000}초 안에 끝나지 않았습니다. 잠시 후 다시 시도하세요.`;
  }
  return error instanceof Error ? error.message : `${job}에 실패했습니다.`;
};

// 표면 손상은 대시보드·보고서 두 탭에서 같은 색이어야 한다. 예전에는 녹색이었는데
// 심각도의 「경미」와 같은 계열이라 유형인지 등급인지 헷갈렸다.
const SURFACE_DAMAGE_COLOR = '#DE8CA5';

const severityLabel: Record<Severity, string> = {
  Critical: '심각',
  Warning: '주의',
  Normal: '경미',
};

const missionStateLabel = (state?: string) => {
  const labels: Record<string, string> = {
    IDLE: '대기',
    READY: '준비',
    RUNNING: '검사 중',
    PAUSED: '일시정지',
    RETURNING: '복귀 중',
    COMPLETED: '검사 완료',
    FAILED_BLOCKED: '주행 차단',
    EMERGENCY_STOP: '긴급 정지',
    EMERGENCY: '긴급 정지',
    ERROR: '오류',
    AUTO: '자율 검사',
    MANUAL: '수동 검사',
    DRIVING: '주행 중',
    MAINTENANCE: '정비 중',
  };
  return state ? labels[state] ?? state : '--';
};

const resolveServerMediaUrl = (path?: string) => {
  if (!path) return '';
  if (/^https?:\/\//i.test(path) || path.startsWith('data:') || path.startsWith('blob:')) return path;
  return `${API_ORIGIN}/${path.replace(/^\//, '')}`;
};

const mapDefectRows = (payload: unknown): Defect[] => {
  const seenDefectKeys = new Set<string>();
  const uniqueRows = asArray(payload).filter((row, index) => {
    // `!== undefined` 로만 보면 안 된다. API 는 값이 없는 필드를 **null 로** 내려보내고
    // `null !== undefined` 는 참이라, 세 필드가 전부 null 인 결함들이 모두 같은 키
    // ["null","null",0] 을 받아 한 건으로 합쳐진다. 실제로 결함 5건이 1건으로 보였다.
    // `!= null` 은 null 과 undefined 를 함께 걸러 낸다.
    const eventKey = row.operation_id != null && row.event_id != null && row.detection_index != null
      ? JSON.stringify([String(row.operation_id), String(row.event_id), Number(row.detection_index)])
      : String(row.id ?? `defect-${index + 1}`);
    if (seenDefectKeys.has(eventKey)) return false;
    seenDefectKeys.add(eventKey);
    return true;
  });
  return uniqueRows.map((row, index) => {
    const detectedAt = textValue(row.detected_at, row.detection_time, row.created_at);
    const detectedTimestamp = parseUtcTimestamp(detectedAt);
    const rawConfidence = numericValue(row.confidence, row.detection_confidence, row.score);
    const rawDistance = numericValue(row.approx_position_m, row.distance_m, row.location_m, row.distance);
    const image = textValue(row.snapshot_url, row.image_url, row.media_url, row.snapshot_path) ?? '';
    return {
      id: String(row.id ?? `defect-${index + 1}`),
      type: textValue(row.defect_type, row.type, row.category) ?? '미분류',
      severity: normalizeSeverity(row.severity ?? row.grade),
      distance: rawDistance === undefined ? '--' : `${rawDistance.toFixed(1)} m`,
      time: Number.isFinite(detectedTimestamp) ? new Date(detectedTimestamp).toLocaleTimeString('en-GB', { hour12: false }) : '--',
      detectedAtMs: Number.isFinite(detectedTimestamp) ? detectedTimestamp : undefined,
      positionM: rawDistance,
      confidence: toPercent(rawConfidence),
      photoReviewed: row.photo_reviewed === true,
      status: textValue(row.repair_status, row.status, row.review_status) ?? '--',
      image: resolveServerMediaUrl(image),
    };
  });
};

const sanitizeRobotLogText = (value?: string) => (value ?? '--')
  .replaceAll('Discord Hermes VL', 'AIagent')
  .replaceAll('Hermes VL', 'AIagent')
  .replaceAll('헤르메스VL', 'AIagent')
  .replaceAll('Discord VL', 'AIagent')
  .replaceAll('기록1', '')
  .replaceAll('inspection-record-001.json', '')
  .replace(/\s+/g, ' ')
  .trim();

const sanitizeRobotLogSource = (value?: string) => sanitizeRobotLogText(value ?? '주행 제어');

const mapDriveLogRows = (payload: unknown): RobotEvent[] => asArray(payload).map((log, index) => {
  const timestamp = textValue(log.logged_at, log.timestamp);
  // `new Date()` 로 바로 읽으면 안 된다. 주행로그의 `logged_at` 은 시간대 표기가 없는
  // UTC 인데, 표준상 시간대가 없으면 **로컬시각으로** 해석되어 KST 기준 9시간 이른
  // 시각이 찍혔다(19:58 로그가 10:58 로 보임). `parseUtcTimestamp` 가 없는 Z 를
  // 붙여 주므로 결함 목록·검사 시간과 같은 기준이 된다.
  const loggedAtMs = parseUtcTimestamp(timestamp);
  return {
    id: String(log.id ?? `drive-log-${index}`),
    time: Number.isFinite(loggedAtMs) ? new Date(loggedAtMs).toLocaleTimeString('en-GB', { hour12: false }) : '--',
    category: sanitizeRobotLogSource(textValue(log.command_source)),
    message: sanitizeRobotLogText(textValue(log.note, log.command, log.agent_state)),
    level: textValue(log.level, log.severity) ?? 'info',
  };
});

const operatorRoleLabel = (role?: string) => {
  if (!role) return '운영자';
  const labels: Record<string, string> = {
    OPERATOR: '운영자',
    SUPERVISOR: '관리 감독자',
    ADMIN: '관리자',
  };
  return labels[role.toUpperCase()] ?? role;
};

const defectTypeLabel = (type: string) => {
  const labels: Record<string, string> = {
    crack: '균열',
    corrosion: '부식',
    blockage: '막힘',
    'surface damage': '표면 손상',
    defect: '결함',
    other: '기타 결함',
    unknown: '미분류',
  };
  return labels[type.toLowerCase().replaceAll('_', ' ')] ?? type;
};

const eventCategoryLabel = (category: string) => {
  const labels: Record<string, string> = {
    system: '시스템',
    navigation: '주행 제어',
    perception: 'AI 인식',
    detection: '결함 탐지',
    mission: '검사 임무',
    safety: '안전 제어',
    agent: 'AI Agent',
  };
  return labels[category.toLowerCase()] ?? category;
};

const reportSummaryLabel = (summary?: string) => {
  if (!summary) return undefined;
  const replacements: Record<string, string> = {
    BLOCKAGE: '막힘',
    CRACK: '균열',
    SURFACE_DAMAGE: '표면 손상',
    DEBRIS: '퇴적물',
    ROOT: '뿌리 침입',
    OTHER: '기타',
    CRITICAL: '심각',
    WARNING: '주의',
    NORMAL: '정상',
  };
  return Object.entries(replacements).reduce(
    (text, [source, target]) => text.replaceAll(source, target),
    summary,
  );
};

function StatusDot({ online = true }: { online?: boolean }) {
  return <span className={`status-dot ${online ? 'online' : 'offline'}`} />;
}

function Sidebar({ page, setPage, open, setOpen, live, robotName }: { page: Page; setPage: (page: Page) => void; open: boolean; setOpen: (value: boolean) => void; live: LiveData; robotName?: string }) {
  const agentConnected = Boolean(live.missionState);
  return (
    <>
      {open && <button className="sidebar-backdrop" aria-label="메뉴 닫기" onClick={() => setOpen(false)} />}
      <aside className={`sidebar ${open ? 'is-open' : ''}`}>
        <div className="nav-caption">주요 메뉴</div>
        <nav className="sidebar-nav">
          {navItems.map(({ id, label, icon: Icon }) => (
            <button key={id} className={page === id ? 'active' : ''} onClick={() => { setPage(id); setOpen(false); }}>
              <Icon size={20} /><span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-spacer" />
        <div className="robot-mini">
          <div className="robot-mini-head"><span><StatusDot online={agentConnected} /> {agentConnected ? '서버 연결' : '서버 연결 대기'}</span><Wifi size={15} /></div>
          <strong>{robotName ?? '로봇 --'}</strong><small>{agentConnected ? 'AI Agent 서버 정상' : 'AI Agent 서버 응답 없음'}</small>
        </div>
        <div className="sidebar-version">PipeVision OS · v2.4.1</div>
      </aside>
    </>
  );
}

function Header({ page, onMenu, operator }: { page: Page; onMenu: () => void; operator?: CloudOperator | null }) {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const pageTitle = page === 'dashboard'
    ? '검사 현황'
    : page === 'monitoring'
      ? '실시간 모니터링'
      : page === 'report'
        ? '검사 보고서'
        : page === 'threeD'
          ? '3D 뷰어'
        : '점검 우선순위';
  const dateTimeLabel = `${now.getFullYear()}.${String(now.getMonth() + 1).padStart(2, '0')}.${String(now.getDate()).padStart(2, '0')} ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
  return (
    <header className="top-header">
      <div className="header-title">
        <button className="mobile-menu" onClick={onMenu}><Menu size={22} /></button>
        <div><div className="breadcrumb"><Home size={12} /> 하수관로 관리 <span>/</span> {pageTitle}</div><h1>{pageTitle}</h1></div>
      </div>
      <div className="header-right">
        <div className="datetime"><Clock3 size={16} /><strong>{dateTimeLabel}</strong></div>
        <div className="profile"><div className="avatar">{operator?.name?.slice(0, 1) ?? '운'}</div><div><strong>{operator?.name ?? '운영자'}</strong><span>{operatorRoleLabel(operator?.role)}</span></div></div>
      </div>
    </header>
  );
}

function TopCards({ live, session, inspection, agentConnected, summary, onOpenDashboard, onForceStop }: { live: LiveData; session: CloudSession | null; inspection: InspectionSetupValues | null; agentConnected: boolean; summary?: AiSummaryData; onOpenDashboard?: () => void; onForceStop?: () => void }) {
  const [, setElapsedTick] = useState(0);
  // 서버 세션의 status 는 **이 브라우저가 점검을 걸어 둔 경우에만** 근거로 삼는다.
  // 예전에는 그냥 썼는데, 중단돼 RUNNING 으로 남은 옛 세션이 목록 맨 위에 있으면
  // 점검을 하지도 않는 화면에서 실행 시간이 계속 올라갔다(9시간 같은 값이 나왔다).
  // Agent 가 실제로 보내는 live.missionState 는 조건 없이 그대로 믿는다.
  const missionState = textValue(live.missionState, inspection ? session?.status : undefined)?.toUpperCase();
  const inspectionActive = Boolean(
    missionState
    && ['PREPARING', 'RUNNING', 'PAUSED', 'BLOCKED', 'STOPPING', 'RETURNING', 'DRIVING', 'EMERGENCY_STOP'].includes(missionState),
  );
  useEffect(() => {
    if (!inspectionActive) return;
    const timer = window.setInterval(() => setElapsedTick((current) => current + 1), 1000);
    return () => window.clearInterval(timer);
  }, [inspectionActive]);
  const startedAt = inspectionActive ? textValue(inspection?.configuredAt, session?.started_at) : undefined;
  const allDetails = [
    // 「구간」 자리에는 구간이 와야 한다. 예전에는 세션에 실린 구간을 못 읽어
    // `live.section`(Agent 의 mission.area = 점검 **위치**)으로 떨어졌고, 그래서
    // 「서울…역삼1동 구간1」로 시작한 점검이 「광주…하남동」으로 보였다.
    // 세션의 survey 를 화면 값 바로 다음에 두어, 점검을 시작하지 않은 화면도
    // 같은 값을 읽게 한다.
    ['점검 구간', inspectionActive ? textValue(inspection?.section, session?.survey?.inspection_section, inspection?.sectionName, session?.survey?.survey_location, live.section, session?.area, session?.region, session?.district_id === undefined ? undefined : `검사 구역 ${session.district_id}`) : undefined],
    ['점검 방향', inspectionActive ? inspectionDirectionLabel(textValue(inspection?.direction, session?.survey?.inspection_direction, session?.direction, session?.inspection_direction)) : undefined],
    // 시작 시각을 먼저 두고 경과를 뒤에 둔다 — 「언제 시작해서 얼마나 됐다」 순서다.
    ['점검 시작', inspectionDateTimeLabel(startedAt)],
    ['실행 시간', inspectionActive ? inspectionElapsedLabel(startedAt) : undefined],
    // 「시작 지점」은 세션에 안 담기는 값이라 늘 '--' 였고, 「점검 진행률」은 구간
    // 전장을 모르면 계산 근거가 없어 뺐습니다. 줄어든 높이는 아래 격자가 받습니다.
  ] as const;
  // 대시보드(summary 를 받는 배치)는 점검 구간·실행 시간만 요약해 보여주고,
  // 로봇 정보 자리에는 진행 상황 요약을 둡니다. 모니터링은 전체를 유지합니다.
  const details = summary
    ? allDetails.filter(([label]) => label === '점검 구간' || label === '실행 시간')
    : allDetails;
  const inspectionInfo = <><div className="card-heading"><div><h3>점검 정보</h3></div>{onOpenDashboard && <button type="button" className="outline-button" onClick={onOpenDashboard}>대시보드 <span>→</span></button>}</div><div className="inspection-info-list">{details.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value ?? '--'}</strong></div>)}</div></>;
  return (
    <section className={`top-cards ${summary ? 'dashboard-summary-cards' : ''}`}>
      <article className="card info-card inspection-info-card">{inspectionInfo}</article>
      {summary ? <DetectionSummary summary={summary} inspectionActive={inspectionActive} /> : (
        <article className="card combined-status-card">
          <section className="combined-status-section robot-card">
            <div className="card-heading"><div><h3>로봇 정보</h3></div></div>
            <div className="robot-info-grid"><div><span>서버 연결</span><strong className={`robot-link ${agentConnected ? 'on' : 'off'}`}>{agentConnected ? '연결됨' : '연결 안 됨'}</strong></div><div><span>기울기</span><strong className="tilt-axis-values"><small>X:</small> {formatNumber(live.roll)} <small>Y:</small> {formatNumber(live.pitch)} <small>Z:</small> {formatNumber(live.yaw)}</strong></div></div>
          </section>
          {onForceStop && <section className="combined-status-section emergency-stop-card"><div className="emergency-stop-body"><button type="button" className="robot-force-stop" onClick={onForceStop}>STOP</button></div></section>}
        </article>
      )}
    </section>
  );
}

function CameraFeed({ sessionId, operationId, frameReady, onOpenMonitoring, onEmergency }: { selectedDefect?: Defect | null; monitoring?: boolean; live: LiveData; sessionId?: string | number; operationId?: string; frameReady: boolean; onOpenMonitoring?: () => void; onEmergency?: () => void }) {
  const [streamFailed, setStreamFailed] = useState(false);
  const [websocketFrameUrl, setWebsocketFrameUrl] = useState('');
  const [frameRevision, setFrameRevision] = useState(0);
  const websocketFrameUrlRef = useRef('');
  const frameTimerRef = useRef<number | null>(null);
  const frameActiveRef = useRef(false);
  const scopedFrameReady = frameReady && sessionId !== undefined && operationId;
  const fallbackFrameUrl = scopedFrameReady
    ? `${API_BASE_URL}/agent/yolo-frame.jpg?session_id=${encodeURIComponent(String(sessionId))}&operation_id=${encodeURIComponent(operationId)}`
    : frameReady
      ? `${API_BASE_URL}/agent/live-frame.jpg`
      : '';
  const frameSocketHttpUrl = new URL('/ws/agent/frame-stream', window.location.origin);
  frameSocketHttpUrl.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const frameWebSocketUrl = frameSocketHttpUrl.toString();

  const replaceWebsocketFrame = useCallback((nextUrl: string) => {
    const previous = websocketFrameUrlRef.current;
    websocketFrameUrlRef.current = nextUrl;
    setWebsocketFrameUrl(nextUrl);
    if (previous) URL.revokeObjectURL(previous);
  }, []);

  useEffect(() => {
    if (!frameReady) {
      replaceWebsocketFrame('');
      setStreamFailed(true);
      return;
    }
    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let reconnectDelay = 500;

    const clearReconnectTimer = () => {
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    };
    const connect = () => {
      if (!active || document.visibilityState === 'hidden') return;
      if (socket && (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN)) return;
      clearReconnectTimer();
      const currentSocket = new WebSocket(frameWebSocketUrl);
      socket = currentSocket;
      currentSocket.binaryType = 'arraybuffer';
      currentSocket.onopen = () => {
        if (socket === currentSocket) reconnectDelay = 500;
      };
      currentSocket.onmessage = (event) => {
        if (!active || socket !== currentSocket) return;
        const blob = event.data instanceof Blob
          ? event.data
          : new Blob([event.data], { type: 'image/jpeg' });
        replaceWebsocketFrame(URL.createObjectURL(blob));
        setStreamFailed(false);
      };
      currentSocket.onerror = () => currentSocket.close();
      currentSocket.onclose = () => {
        if (!active || socket !== currentSocket) return;
        socket = null;
        replaceWebsocketFrame('');
        setStreamFailed(true);
        if (document.visibilityState === 'hidden') return;
        clearReconnectTimer();
        reconnectTimer = window.setTimeout(() => {
          reconnectTimer = null;
          connect();
        }, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 10_000);
      };
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden') {
        clearReconnectTimer();
        socket?.close();
      } else {
        connect();
      }
    };
    document.addEventListener('visibilitychange', onVisibilityChange);
    connect();
    return () => {
      active = false;
      document.removeEventListener('visibilitychange', onVisibilityChange);
      clearReconnectTimer();
      const currentSocket = socket;
      socket = null;
      currentSocket?.close();
      replaceWebsocketFrame('');
    };
  }, [frameReady, frameWebSocketUrl, replaceWebsocketFrame]);

  const clearFrameTimer = () => {
    if (frameTimerRef.current !== null) window.clearTimeout(frameTimerRef.current);
    frameTimerRef.current = null;
  };
  const requestNextFrame = () => {
    frameTimerRef.current = null;
    if (!frameActiveRef.current || document.visibilityState === 'hidden') return;
    setFrameRevision((current) => current + 1);
  };
  const scheduleNextFrame = (delayMs?: number) => {
    clearFrameTimer();
    frameTimerRef.current = delayMs === undefined
      ? window.setTimeout(requestNextFrame, 120)
      : window.setTimeout(requestNextFrame, delayMs);
  };
  useEffect(() => {
    frameActiveRef.current = Boolean(fallbackFrameUrl && !websocketFrameUrl);
    if (!websocketFrameUrl) setStreamFailed(!fallbackFrameUrl);
    clearFrameTimer();
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'hidden') clearFrameTimer();
      else if (frameActiveRef.current) scheduleNextFrame(0);
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      frameActiveRef.current = false;
      clearFrameTimer();
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [fallbackFrameUrl, websocketFrameUrl]);
  const separator = fallbackFrameUrl.includes('?') ? '&' : '?';
  const refreshedFallbackUrl = fallbackFrameUrl
    ? `${fallbackFrameUrl}${separator}frame=${frameRevision}`
    : '';
  const displayedFrameUrl = websocketFrameUrl || refreshedFallbackUrl;

  return (
    <article className="card camera-card">
      <div className="section-header">
        <div><h2>관로 검사 카메라</h2></div>
        {onOpenMonitoring && <button className="outline-button" onClick={onOpenMonitoring}>모니터링 <span>→</span></button>}
        {onEmergency && (
          <button className="control-btn emergency" style={{ minHeight: 38, padding: '6px 14px' }} onClick={onEmergency}>
            <CircleStop size={17} /><span>긴급 정지</span>
          </button>
        )}
      </div>
      <div className="camera-viewport">
        {displayedFrameUrl && <img src={displayedFrameUrl} alt="AI 관로 검사 카메라 영상" style={{ opacity: streamFailed ? 0 : 1 }} onLoad={() => { setStreamFailed(false); if (!websocketFrameUrl) scheduleNextFrame(); }} onError={() => { setStreamFailed(true); if (!websocketFrameUrl) scheduleNextFrame(800); }} />}
      </div>
    </article>
  );
}

function DetectionSummary({ summary, inspectionActive }: { summary: AiSummaryData; inspectionActive: boolean }) {
  // 「점검 이전」은 **요약이 없을 때** 띄운다. 예전에는 inspectionActive 만 보고
  // 덮어썼는데, 그 값은 Agent 의 missionState 에서 나오므로 점검을 시작해도 첫
  // 텔레메트리가 오기 전까지는 꺼져 있다. 그동안 실제 요약이 들어와도 「점검 이전」에
  // 가려졌다. 요약이 있다는 것 자체가 점검이 돌고 있다는 뜻이므로 그걸 믿는다.
  // 점검을 끝내면 요약이 idle 로 초기화되므로 다시 「점검 이전」이 뜬다.
  const hasSummary = summary.tone === 'ready' || summary.tone === 'pending';
  const tone = hasSummary ? summary.tone : 'idle';
  const message = hasSummary ? summary.message : '점검 이전';
  return (
    <article className="card detection-summary">
      <div className="ai-summary-header">
        <div><h3>진행 상황 요약</h3></div>
      </div>
      <div className={`ai-summary-chat ${tone}`}>
        <span className="ai-chatbot" aria-hidden="true"><Bot size={25} /></span>
        <div className="ai-summary-bubble"><p>{message}</p></div>
      </div>
      {hasSummary && <time className="ai-summary-time">최근 업데이트&nbsp; {summary.time}</time>}
    </article>
  );
}

function InspectionSetupModal({
  values,
  robots,
  operators,
  onChange,
  onClose,
  onSubmit,
}: {
  values: InspectionSetupValues;
  robots: CloudRobot[];
  operators: CloudOperator[];
  onChange: (field: keyof InspectionSetupValues, value: string) => void;
  onClose: () => void;
  onSubmit: () => void;
}) {
  // 「점검 시작 시간」칸을 1초마다 갱신한다. 모달이 열려 있는 동안만 돈다.
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const sectionOptions = sectionOptionsFor(values.sectionName);
  const isComplete = Boolean(
    values.sectionName
    && values.section
    && values.robotId
    && values.operatorId
    && values.operatorCode
    && values.direction
    && values.districtId !== undefined
  );

  return (
    <div className="inspection-setup-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="inspection-setup-modal" role="dialog" aria-modal="true" aria-labelledby="inspection-setup-title">
        <header className="inspection-setup-header">
          <div><span className="inspection-setup-icon"><SlidersHorizontal size={22} /></span><h2 id="inspection-setup-title">점검 기본 설정</h2></div>
          <button type="button" className="inspection-setup-close" onClick={onClose} aria-label="팝업 닫기"><X size={22} /></button>
        </header>

        <form className="inspection-setup-form" onSubmit={(event) => { event.preventDefault(); if (isComplete) onSubmit(); }}>
          <label className="inspection-field inspection-field-full">
            <span>점검 위치</span>
            <input value={values.sectionName} onChange={(event) => onChange('sectionName', event.target.value)} placeholder="점검 위치 작성" autoFocus />
          </label>

          {/* 구간 목록은 동 면적으로 정해진 개수(3~6개)만큼 나온다. 위치를 손으로
              고쳐 표에 없는 동이 되면 목록이 비므로 안내 문구를 대신 띄운다. */}
          <label className="inspection-field inspection-field-full">
            <span>점검 구간</span>
            <select
              value={values.section}
              onChange={(event) => onChange('section', event.target.value)}
              disabled={sectionOptions.length === 0}
            >
              <option value="" disabled>{sectionOptions.length === 0 ? '점검 위치를 먼저 선택하세요' : '선택'}</option>
              {sectionOptions.map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
          </label>

          {/* 승인 운영자와 확인 코드는 한 쌍이라 같은 줄에 둔다. 떨어져 있으면
              어느 운영자의 코드인지 눈으로 이어 붙여야 한다. */}
          <label className="inspection-field">
            <span>승인 운영자</span>
            <select value={values.operatorId} onChange={(event) => onChange('operatorId', event.target.value)}>
              <option value="" disabled>선택</option>
              {operators.map((item) => <option key={String(item.id)} value={String(item.id)}>{item.name ?? `운영자 ${item.id}`}{item.role ? ` (${item.role})` : ''}</option>)}
            </select>
          </label>

          {/* 운영자를 고르기 전에는 어느 코드를 넣어야 할지 알 수 없으므로 잠가 둔다. */}
          <label className="inspection-field">
            <span>확인 코드</span>
            <input
              type="password"
              value={values.operatorCode}
              onChange={(event) => onChange('operatorCode', event.target.value)}
              placeholder={values.operatorId ? '운영자 확인 코드' : '운영자를 먼저 선택하세요'}
              disabled={!values.operatorId}
              autoComplete="off"
            />
          </label>

          <label className="inspection-field inspection-field-robot">
            <span>점검 로봇</span>
            <select value={values.robotId} onChange={(event) => onChange('robotId', event.target.value)}>
              <option value="" disabled>선택</option>
              {robots.map((item) => <option key={String(item.id)} value={String(item.id)}>{item.name ?? `로봇 ${item.id}`}{item.model ? ` (${item.model})` : ''}</option>)}
            </select>
          </label>

          <label className="inspection-field inspection-field-direction">
            <span>점검 방향</span>
            <select value={values.direction} onChange={(event) => onChange('direction', event.target.value)}>
              <option value="" disabled>선택</option>
              <option value="FORWARD">정방향 (→)</option>
              <option value="REVERSE">역방향 (←)</option>
              <option value="BOTH">양방향 (↔)</option>
            </select>
          </label>

          <label className="inspection-field inspection-field-time">
            <span>점검 시작 시간</span>
            {/* 지금 시각을 초까지 흘려 보여 준다. 예전에는 `values.configuredAt`
                (모달을 연 시각)이 굳어 있어, 설정하는 동안 시간이 멈춘 것처럼 보였다.
                실제로 기록되는 값은 「점검 시작」을 누른 순간 다시 찍으므로
                (`submitInspectionSetup`), 화면의 이 시계와 같은 값이 들어간다. */}
            <input value={inspectionDateTimeLabel(now.toISOString()) ?? ''} readOnly aria-readonly="true" />
          </label>

          <footer className="inspection-setup-actions">
            <button type="button" className="inspection-cancel" onClick={onClose}>취소</button>
            <button type="submit" className="inspection-submit" disabled={!isComplete}>점검 시작</button>
          </footer>
        </form>
      </section>
    </div>
  );
}

// 로봇을 움직이는 조작 앞에 세우는 확인 코드 팝업. 코드가 맞는지는 **서버가**
// 판단한다(`POST /operators/:id/verify-code`). 화면에서 비교하면 개발자 도구로 값을
// 읽어 갈 수 있다. 통과하면 그때 실제 명령이 나간다.
function OperatorCodePrompt({ title, operator, onCancel, onVerified }: { title: string; operator: CloudOperator | null; onCancel: () => void; onVerified: () => void }) {
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const submit = async () => {
    if (!code || checking) return;
    if (operator?.id === undefined) {
      setError('승인 운영자를 알 수 없습니다. 점검을 다시 시작하세요.');
      return;
    }
    setChecking(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/operators/${String(operator.id)}/verify-code`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code }),
      });
      if (!response.ok) {
        const detail = asRecord(await response.json().catch(() => ({}))).detail;
        throw new Error(typeof detail === 'string' && detail ? detail : '확인 코드가 올바르지 않습니다.');
      }
      onVerified();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '확인 코드가 올바르지 않습니다.');
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="inspection-setup-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onCancel(); }}>
      <section className="inspection-result-modal operator-code-modal" role="dialog" aria-modal="true" aria-labelledby="operator-code-title">
        <h2 id="operator-code-title">{title}</h2>
        <p className="inspection-result-summary">{operator?.name ? `${operator.name} 운영자의 확인 코드를 입력하세요.` : '승인 운영자의 확인 코드를 입력하세요.'}</p>
        <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
          <input
            className="operator-code-input"
            type="password"
            value={code}
            onChange={(event) => { setCode(event.target.value); setError(null); }}
            placeholder="확인 코드"
            autoComplete="off"
            autoFocus
          />
          {error && <p className="report-picker-error" role="alert">{error}</p>}
          <div className="inspection-result-actions">
            <button type="button" className="inspection-cancel" onClick={onCancel}>취소</button>
            <button type="submit" className="inspection-submit" disabled={!code || checking}>{checking ? '확인 중…' : '확인'}</button>
          </div>
        </form>
      </section>
    </div>
  );
}

function ControlPanel({ state, onAction, onEndInspection, ending }: { state: RobotState; onAction: (state: RobotState) => void; onEndInspection: () => void; ending: boolean }) {
  const running = state === 'running';
  const paused = state === 'paused';
  // 로봇이 스스로 안전 정지한 상태. 주행은 멈췄지만 **점검은 아직 안 끝났다** —
  // 세션이 열려 있고 운영자가 종료를 눌러야 닫힌다. 예전에는 이 상태를 「점검 중이
  // 아님」으로 봐서 「점검 종료」까지 잠겼고, 그래서 세션을 닫을 방법이 없었다.
  const emergency = state === 'emergency';
  const inspectionActive = running || paused;
  const canEnd = inspectionActive || emergency;
  // 한 버튼이 「일시 정지」와 「다시 시작」을 오간다. 두 개를 나란히 두면 지금 어느
  // 쪽이 유효한지 매번 읽어야 하고, 멈춘 상태에서 또 멈추는 일이 생긴다.
  //
  // 기준은 **멈춰 있는가**(paused) 하나다. 「점검 중인가」로 가르면 점검 전 대기
  // 상태에서 「다시 시작」이 떠서, 돌지도 않는 것을 다시 시작하라는 말이 된다.
  return (
    <article className="card control-panel">
      <div className="section-header exploration-round"><strong>로봇 제어</strong></div>
      <div className="control-grid">
        <button
          className={`control-btn start ${paused ? 'resume' : 'pause'}`}
          disabled={!inspectionActive}
          onClick={() => onAction(paused ? 'running' : 'paused')}
        >
          {paused
            ? <><Play size={18} /><span>다시 시작</span></>
            : <><i className="control-stop-shape" /><span>일시 정지</span></>}
        </button>
        <button className="control-btn finish" disabled={!canEnd || ending} onClick={onEndInspection}><CircleStop size={20} /><span>{ending ? '종료 처리 중…' : '점검 종료'}</span></button>
      </div>
      <div className="direction-control" aria-label="로봇 방향 제어">
        <div className="direction-pad">
          <button type="button" className="direction-up" aria-label="전진" disabled><i className="direction-shape arrow-up" /></button>
          <button type="button" className="direction-left" aria-label="왼쪽 이동" disabled><i className="direction-shape arrow-left" /></button>
          <button type="button" className="direction-right" aria-label="오른쪽 이동" disabled><i className="direction-shape arrow-right" /></button>
          <button type="button" className="direction-down" aria-label="후진" disabled><i className="direction-shape arrow-down" /></button>
        </div>
      </div>
    </article>
  );
}

function DonutChart({ segments, total }: { segments: Array<{ value: number; color: string }>; total: number }) {
  let offset = 0;
  return (
    <div className="donut-shell">
      <svg className="donut" viewBox="0 0 42 42" aria-hidden="true">
        <circle cx="21" cy="21" r="15.9" fill="transparent" stroke="#EEF1F7" strokeWidth="5" />
        {segments.map((segment) => { const currentOffset = offset; offset += segment.value; return <circle key={segment.color} cx="21" cy="21" r="15.9" fill="transparent" stroke={segment.color} strokeWidth="5" strokeDasharray={`${segment.value} ${100 - segment.value}`} strokeDashoffset={25 - currentOffset} />; })}
      </svg>
      <div className="donut-center"><b>{total}</b><span>결함</span></div>
    </div>
  );
}

function DefectStatistics({ defects }: { defects: Defect[] }) {
  // 통계는 **판독이 끝난 결함만** 센다. 목록은 탐지 즉시 보여 주지만(로봇이 지금
  // 무엇을 보고 있는지가 중요하다), 통계까지 먼저 세면 판독이 오탐을 걸러낼 때마다
  // 숫자가 줄어들어 계산이 틀린 것처럼 보인다.
  const counted = defects.filter((item) => item.photoReviewed);
  const categoryDefinitions = [['Crack', '균열', '#4F7CF7'], ['Blockage', '막힘', '#8B5CF6'], ['Surface Damage', '표면 손상', SURFACE_DAMAGE_COLOR]] as const;
  const categories = categoryDefinitions.map(([name, label, color]) => {
    const count = counted.filter((item) =>
      item.type.toLowerCase().includes(name.toLowerCase().split(' ')[0])
      || item.type.includes(label.split(' ')[0]),
    ).length;
    return { name, label, color, count, value: counted.length ? count / counted.length * 100 : 0 };
  });
  const severityCounts = {
    Critical: counted.filter((item) => item.severity === 'Critical').length,
    Warning: counted.filter((item) => item.severity === 'Warning').length,
    Normal: counted.filter((item) => item.severity === 'Normal').length,
  };
  const severityWidth = (count: number) => counted.length ? `${count / counted.length * 100}%` : '0%';
  return (
    <article className="card statistics-card">
      <section className="category-chart"><div className="chart-title"><span>결함 통계</span></div><div className="statistics-body"><div className="donut-wrap"><DonutChart total={counted.length} segments={categories.map(({ value, color }) => ({ value, color }))} /></div><div className="legend-list">{categories.map(({ name, label, count, color, value }) => <div className="category-row" key={name}><span><i style={{ background: color }} />{label}</span><div className="category-bar"><i style={{ width: `${value}%`, background: color }} /></div><b>{count}</b></div>)}</div></div></section>
      <section className="severity-chart"><div className="chart-title"><span>심각도별 결함</span></div><div className="severity-bars"><div className="bar-row critical"><span>심각</span><div><i className="critical-bar" style={{ width: severityWidth(severityCounts.Critical) }} /></div><b>{severityCounts.Critical}</b></div><div className="bar-row warning"><span>주의</span><div><i className="warning-bar" style={{ width: severityWidth(severityCounts.Warning) }} /></div><b>{severityCounts.Warning}</b></div><div className="bar-row normal"><span>경미</span><div><i className="normal-bar" style={{ width: severityWidth(severityCounts.Normal) }} /></div><b>{severityCounts.Normal}</b></div></div></section>
    </article>
  );
}

function DashboardPage({ defects, robotState, onRobotAction, setPage, live, agentConnected, agentIdentity, aiSummary, session, inspection, onEndInspection, ending }: { defects: Defect[]; robotState: RobotState; onRobotAction: (state: RobotState) => void; setPage: (page: Page) => void; live: LiveData; agentConnected: boolean; agentIdentity: { sessionId?: string | number; operationId?: string }; aiSummary: AiSummaryData; session: CloudSession | null; inspection: InspectionSetupValues | null; onEndInspection: () => void; ending: boolean }) {
  // 「점검 정보」는 모니터링 탭에도 같은 카드가 있어 대시보드에서는 뺐다. 그 자리에
  // 결함 통계를 올리고, 오른쪽 기둥을 위아래로 나눠 진행 상황 요약과 로봇 제어를 담는다.
  // status 를 언제 믿는지는 TopCards 와 같은 규칙이다 — 옛 RUNNING 세션이 되살아나면 안 된다.
  const missionState = textValue(live.missionState, inspection ? session?.status : undefined)?.toUpperCase();
  const inspectionActive = Boolean(
    missionState
    && ['PREPARING', 'RUNNING', 'PAUSED', 'BLOCKED', 'STOPPING', 'RETURNING', 'DRIVING', 'EMERGENCY_STOP'].includes(missionState),
  );
  // 점검 중이 아니면 지난 점검의 결함을 남기지 않는다. 폴링이 서버에서 가장 최근
  // 세션과 그 결함을 다시 실어 오기 때문에, 걸러 주지 않으면 점검을 끝낸 뒤에도
  // 통계가 살아 있어 아직 점검 중인 것처럼 보인다. 모니터링 탭은 이미 같은 방식으로
  // 비우고 있어서 두 탭이 서로 달라 보이기까지 했다.
  const visibleDefects = inspectionActive ? defects : [];
  // 카메라는 **일부러** 점검 여부와 무관하게 계속 띄운다. 로봇·Agent 연결을 눈으로
  // 확인할 수 있어야 디버깅이 되기 때문이다. 시연에서는 점검 중이 아닐 때 끌 예정이라,
  // 그때는 아래를 `agentConnected && inspectionActive` 로 바꾸면 된다.
  const cameraReady = agentConnected;
  return (
    <>
      <DefectStatistics defects={visibleDefects} />
      <section className="dashboard-grid">
        <CameraFeed live={live} sessionId={inspection?.sessionId ?? agentIdentity.sessionId} operationId={inspection?.operationId ?? agentIdentity.operationId} frameReady={cameraReady} onOpenMonitoring={() => setPage('monitoring')} />
        <div className="dashboard-side">
          <DetectionSummary summary={aiSummary} inspectionActive={inspectionActive} />
          <ControlPanel state={robotState} onAction={onRobotAction} onEndInspection={onEndInspection} ending={ending} />
        </div>
      </section>
    </>
  );
}

function RobotLog({ events, active }: { events: RobotEvent[]; active: boolean }) {
  const dragScroll = useDragScroll<HTMLDivElement>();
  return <article className="card log-card"><div className="section-header"><div><h2>로봇 로그</h2></div></div><div className="log-list draggable-scroll" {...dragScroll}>{events.length === 0 ? <div className="empty-state">{active ? '수신된 로봇 이벤트가 없습니다.' : '검사 시작 후 실시간 로그가 표시됩니다.'}</div> : events.map((event) => <div className={`log-item ${event.level}`} key={event.id}><time>{event.time}</time><i /><div><strong>{eventCategoryLabel(event.category)}</strong><span>{event.message}</span></div></div>)}</div></article>;
}

function DefectTable({ defects, selected, setSelected }: { defects: Defect[]; selected: Defect | null; setSelected: (defect: Defect) => void }) {
  const dragScroll = useDragScroll<HTMLDivElement>();
  return (
    <article className="card defect-table-card">
      <div className="section-header"><div><h2>탐지 결함 목록</h2></div></div>
      <div className="table-scroll draggable-scroll" {...dragScroll}><table className="defect-table"><thead><tr><th>결함 이미지</th><th>결함 유형</th><th>탐지 시각</th><th>신뢰도</th><th>상태</th></tr></thead><tbody>
        {defects.length === 0
          ? <tr><td colSpan={5}><div className="empty-state">현재 세션에서 조회된 결함이 없습니다.</div></td></tr>
          : defects.map((defect) => <tr key={defect.id} className={selected?.id === defect.id ? 'selected' : ''} onClick={() => setSelected(defect)}><td><div className="defect-thumb">{defect.image && <img src={defect.image} alt="" onError={(event) => { event.currentTarget.style.display = 'none'; }} />}<span>{defect.id}</span></div></td><td><strong>{defectTypeLabel(defect.type)}</strong></td><td>{defect.time}</td><td><div className="confidence-cell"><strong>{defect.confidence === undefined ? '--' : `${defect.confidence.toFixed(1)}%`}</strong><div><i style={{ width: `${defect.confidence ?? 0}%` }} /></div></div></td><td><span className={`status-text severity-${defect.severity.toLowerCase()}`}><i className="severity-dot" aria-hidden="true" />{severityLabel[defect.severity]}</span></td></tr>)}
      </tbody></table></div>
    </article>
  );
}

// `selected` 는 「보고서를 골랐는가」다. `report` 로 대신 판단하면 불러오는 동안
// 잠깐 미선택으로 보이고, 결함이 0건인 보고서와 미선택을 구분할 수도 없다.
function ReportDefectList({ defects, report, selected }: { defects: Defect[]; report: CloudReport | null; selected: boolean }) {
  // 「1등급 (심각)」처럼 등급 번호를 붙이지 않는다. 여기 숫자는 지침의 상태등급
  // (보고서 본문의 구조적·운영적 등급)과 다른 값이라, 나란히 놓이면 같은 것으로
  // 읽힌다. 화면 다른 곳과 같은 `severityLabel` 을 쓴다.
  return (
    <section className="card report-defect-list">
      <div className="section-header"><div><h2>탐지 결함 목록</h2></div></div>
      <div className="report-defect-list-body">
        {defects.length === 0
          ? <div className="empty-state">{selected ? '선택한 보고서에서 조회된 결함이 없습니다.' : '보고서를 선택해주세요.'}</div>
          : defects.map((defect) => (
            <article className="report-defect-detail" key={defect.id}>
              <div className="report-defect-visual">
                {defect.image ? <img src={defect.image} alt={`${defectTypeLabel(defect.type)} 결함`} onError={(event) => { event.currentTarget.style.display = 'none'; }} /> : <div className="report-defect-image-empty">결함 이미지 없음</div>}
              </div>
              <div className="report-defect-info">
                <div className="report-defect-title"><h3>{defectTypeLabel(defect.type)}</h3><span className={severityStyle[defect.severity]}>{severityLabel[defect.severity]}</span></div>
                {/* 「결함 내역」은 바로 위 제목과 같은 값이라 뺐다. 보수 상태 변경은
                    점검 결과를 보는 화면의 일이 아니라 별도 관리 기능이므로 함께 걷어냈다. */}
                <div className="report-defect-meta"><div><span>날짜</span><strong>{report?.inspection_date ?? '정보 없음'}</strong></div><div><span>시간</span><strong>{defect.time}</strong></div></div>
              </div>
            </article>
          ))}
      </div>
    </section>
  );
}

function MonitoringPage({ defects, live, agentConnected, events, session, inspection, robotState, setPage, onForceStop }: { defects: Defect[]; live: LiveData; agentConnected: boolean; events: RobotEvent[]; session: CloudSession | null; inspection: InspectionSetupValues | null; robotState: RobotState; setPage: (page: Page) => void; onForceStop: () => void }) {
  const inspectionActive = robotState === 'running' || robotState === 'paused' || robotState === 'returning';
  const visibleDefects = inspectionActive ? defects : [];
  const visibleEvents = inspectionActive ? events : [];
  const [selected, setSelected] = useSessionState<Defect | null>('pipecheck.monitoring.selected-defect', null);
  return <><TopCards live={live} session={session} inspection={inspection} agentConnected={agentConnected} onOpenDashboard={() => setPage('dashboard')} onForceStop={onForceStop} /><section className="monitoring-grid"><RobotLog events={visibleEvents} active={inspectionActive} /><DefectTable defects={visibleDefects} selected={selected} setSelected={setSelected} /></section></>;
}

type ViewerStatus = 'idle' | 'loading' | 'ready' | 'error';

// public/splats/ 에 포함해 배포하는 씬. 추후 보고서별 모델 URL 로 대체 예정.
// 새 씬 추가: scripts/create-ksplat.mjs 로 변환해 public/splats/<이름>.ksplat 으로 복사.
const SPLAT_SCENE = 'p5_w3';

function ThreeDPage({ session, report: initialReport, reports }: { session: CloudSession | null; report: CloudReport | null; reports: CloudReport[] }) {
  const defaultDate = initialReport?.inspection_date ?? session?.started_at?.slice(0, 10) ?? '';
  const [selectedDate, setSelectedDate] = useSessionState('pipecheck.3d.selected-date', defaultDate);
  const [selectedReportId, setSelectedReportId] = useSessionState('pipecheck.3d.selected-report', initialReport ? String(initialReport.id) : '');
  const [generatedReportId, setGeneratedReportId] = useSessionState('pipecheck.3d.generated-report', '');
  const [viewerStatus, setViewerStatus] = useState<ViewerStatus>('idle');
  const [viewerError, setViewerError] = useState('');
  const [loadProgress, setLoadProgress] = useState(0);
  const viewerContainerRef = useRef<HTMLDivElement | null>(null);
  const splatViewerRef = useRef<GaussianSplats3D.Viewer | null>(null);
  const [calendarMonth, setCalendarMonth] = useState(() => {
    const date = defaultDate ? new Date(`${defaultDate}T00:00:00`) : new Date();
    return new Date(date.getFullYear(), date.getMonth(), 1);
  });

  // 보고서 탭과 같은 이유로 첫 진입에서만 심는다 — `selectedDate` 가 의존성이라
  // 날짜를 누를 때마다(그때 선택을 비운다) 최신 보고서가 곧바로 되살아났다.
  const seededSelection = useRef(false);
  useEffect(() => {
    if (!defaultDate || seededSelection.current) return;
    seededSelection.current = true;
    setSelectedDate((current) => current || defaultDate);
    setSelectedReportId((current) => current || (initialReport ? String(initialReport.id) : ''));
    setCalendarMonth((current) => selectedDate ? current : (() => {
      const date = new Date(`${defaultDate}T00:00:00`);
      return new Date(date.getFullYear(), date.getMonth(), 1);
    })());
  }, [defaultDate, initialReport?.id, selectedDate]);

  // 같은 날짜에 여러 건이면 번호가 빠른 것이 위로. 보고서 탭과 같은 규칙이다.
  const reportsForDate = reports
    .filter((item) => item.inspection_date === selectedDate)
    .sort((left, right) => {
      const key = (item: CloudReport) => item.report_no?.replace(/^R-/, '') ?? String(item.id).padStart(12, '0');
      return key(left).localeCompare(key(right));
    });
  const reportDates = new Set(reports.flatMap((item) => item.inspection_date ? [item.inspection_date] : []));
  const calendarYear = calendarMonth.getFullYear();
  const calendarMonthIndex = calendarMonth.getMonth();
  const leadingDays = new Date(calendarYear, calendarMonthIndex, 1).getDay();
  const daysInMonth = new Date(calendarYear, calendarMonthIndex + 1, 0).getDate();
  const calendarDays = Array.from({ length: leadingDays + daysInMonth }, (_, index) => index < leadingDays ? null : index - leadingDays + 1);
  const calendarDateKey = (day: number) => `${calendarYear}-${String(calendarMonthIndex + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
  // 그 달에 보고서가 있는 날이 몇 날인지 — 헤더에 적어 달을 넘겨야 할지 알려준다.
  const monthReportDays = [...reportDates].filter((date) => date.startsWith(`${calendarYear}-${String(calendarMonthIndex + 1).padStart(2, '0')}`)).length;

  useEffect(() => () => {
    void splatViewerRef.current?.dispose().catch(() => {});
    splatViewerRef.current = null;
  }, []);

  const startViewer = async () => {
    if (!viewerContainerRef.current || splatViewerRef.current) return;
    setViewerError('');
    setLoadProgress(0);
    setViewerStatus('loading');
    try {
      const viewer = new GaussianSplats3D.Viewer({
        rootElement: viewerContainerRef.current,
        // SharedArrayBuffer 는 COOP/COEP 헤더가 있어야 해서 끈다. 없으면 생성 자체가 실패.
        sharedMemoryForWorkers: false,
        // 각도에 따른 색 변화(SH)를 살린다. ksplat 변환도 2차까지 담아서 내보냈다.
        sphericalHarmonicsDegree: 2,
        // p5_w3 씬의 첫 촬영 프레임 시점 (gsplat Parser 의 normalize 를 적용한
        // colmap 포즈에서 추출 — 학습 좌표계 기준이라 PLY/ksplat 과 같은 공간).
        // lookAt 은 파이프 중심부(씬 포인트 중앙값). 휠 줌 이동량이 목표점까지의
        // 거리에 비례하므로 목표점이 가까우면 줌이 답답해진다.
        cameraUp: [-0.246, -0.927, -0.282],
        initialCameraPosition: [3.793, -0.547, -0.082],
        initialCameraLookAt: [0.009, -0.032, -0.008],
      });
      splatViewerRef.current = viewer;
      await viewer.addSplatScene(`/splats/${SPLAT_SCENE}.ksplat`, {
        showLoadingUI: false,
        splatAlphaRemovalThreshold: 5,
        onProgress: (percent) => setLoadProgress(Math.round(percent)),
      });
      viewer.start();
      setGeneratedReportId(selectedReportId);
      setViewerStatus('ready');
    } catch (error) {
      void splatViewerRef.current?.dispose().catch(() => {});
      splatViewerRef.current = null;
      setViewerError(error instanceof Error ? error.message : '3D 모델을 불러오지 못했습니다.');
      setViewerStatus('error');
    }
  };

  useEffect(() => {
    if (viewerStatus !== 'idle' || !selectedReportId || generatedReportId !== selectedReportId) return;
    void startViewer();
  }, [generatedReportId, selectedReportId, viewerStatus]);

  return (
    <section className="three-d-page">
      {/* 달력을 접어 두면 어느 날짜에 점검 기록이 있는지 열어 보기 전엔 알 수
          없다. 상시 노출하고 보고서 있는 날을 표시해 날짜부터 고르게 한다. */}
      <aside className="three-d-picker">
        <div className="three-d-picker-block">
          <header className="three-d-picker-head">
            <span className="three-d-picker-title"><CalendarDays size={16} />점검 날짜</span>
            <span className="three-d-picker-note">{monthReportDays > 0 ? `이 달 기록 ${monthReportDays}일` : '이 달 기록 없음'}</span>
          </header>
          <div className="three-d-calendar">
            <div className="report-calendar-header">
              <button type="button" aria-label="이전 달" onClick={() => setCalendarMonth(new Date(calendarYear, calendarMonthIndex - 1, 1))}><ArrowLeft size={16} /></button>
              <strong>{calendarYear}년 {calendarMonthIndex + 1}월</strong>
              <button type="button" aria-label="다음 달" onClick={() => setCalendarMonth(new Date(calendarYear, calendarMonthIndex + 1, 1))}><ArrowRight size={16} /></button>
            </div>
            <div className="report-calendar-weekdays">{['일', '월', '화', '수', '목', '금', '토'].map((day) => <span key={day}>{day}</span>)}</div>
            <div className="report-calendar-days">
              {calendarDays.map((day, index) => day === null ? <i key={`blank-${index}`} /> : (() => {
                const dateKey = calendarDateKey(day);
                const hasReport = reportDates.has(dateKey);
                return (
                  <button
                    type="button"
                    key={dateKey}
                    aria-pressed={selectedDate === dateKey}
                    title={hasReport ? `${dateKey} — 점검 기록 있음` : `${dateKey} — 점검 기록 없음`}
                    className={`${hasReport ? 'has-report' : ''} ${selectedDate === dateKey ? 'selected' : ''}`}
                    onClick={() => { setSelectedDate(dateKey); setSelectedReportId(''); }}
                  >
                    {day}
                  </button>
                );
              })())}
            </div>
          </div>
          <p className="three-d-calendar-legend"><i className="has-report" />점검 기록 있는 날</p>
        </div>

        <div className="three-d-picker-block">
          <header className="three-d-picker-head">
            {/* 고른 날짜는 달력에 이미 칠해져 있어 여기 또 적을 필요가 없다. */}
            <span className="three-d-picker-title">보고서</span>
          </header>
          {reportsForDate.length === 0 ? (
            <p className="three-d-empty">{selectedDate ? '이 날짜에는 점검 보고서가 없습니다.' : '달력에서 날짜를 고르세요.'}</p>
          ) : (
            <ul className="three-d-report-list">
              {reportsForDate.map((item) => {
                const id = String(item.id);
                return (
                  <li key={id}>
                    <button
                      type="button"
                      aria-pressed={selectedReportId === id}
                      className={selectedReportId === id ? 'selected' : ''}
                      onClick={() => setSelectedReportId(id)}
                    >
                      {/* R- 접두어는 모든 번호에 똑같이 붙어 정보가 없습니다. */}
                      <strong>{item.report_no?.replace(/^R-/, '') ?? `보고서 ${item.id}`}</strong>
                      <span>{item.region ?? `세션 ${item.session_id}`}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {/* 보고서를 고르지 않고 생성하면 어느 점검의 결과인지 알 수 없습니다. */}
        <button
          className="primary-button three-d-generate"
          disabled={!selectedReportId || viewerStatus === 'loading' || viewerStatus === 'ready'}
          onClick={() => void startViewer()}
        >
          {viewerStatus === 'loading' ? '3D 모델 로딩 중' : viewerStatus === 'ready' ? '생성 완료' : '3D 재구성 생성'}
        </button>
        {!selectedReportId && viewerStatus === 'idle' && (
          <p className="three-d-generate-hint">보고서를 선택하면 생성할 수 있습니다.</p>
        )}
      </aside>

      <div className="three-d-viewer-panel">
        <div className="three-d-viewer-canvas" ref={viewerContainerRef} />
        {viewerStatus !== 'ready' && (
          <div className="three-d-viewer-placeholder">
            {viewerStatus === 'loading' && <><span className="three-d-viewer-spinner" /><p>3D 모델을 불러오는 중입니다{loadProgress > 0 ? ` (${loadProgress}%)` : ''}</p></>}
            {viewerStatus === 'idle' && <p>날짜와 보고서를 고른 뒤 생성을 누르면 3D 재구성 결과가 표시됩니다.</p>}
            {viewerStatus === 'error' && <p className="three-d-viewer-error">{viewerError || '3D 모델을 불러오지 못했습니다.'}</p>}
          </div>
        )}
      </div>
    </section>
  );
}

function ReportPage({ session, report: initialReport, reports }: { session: CloudSession | null; report: CloudReport | null; reports: CloudReport[] }) {
  const defaultDate = initialReport?.inspection_date ?? session?.started_at?.slice(0, 10) ?? '';
  const [selectedDate, setSelectedDate] = useSessionState('pipecheck.report.selected-date', defaultDate);
  const [selectedReportId, setSelectedReportId] = useSessionState('pipecheck.report.selected-report', initialReport ? String(initialReport.id) : '');
  // 'summary' = 요약 생성 중(LLM), 'pdf' = PDF 렌더링 중. 단계를 나눠 보여 주는 이유는
  // 둘의 소요 시간이 각각 20~35초 / 23~58초라 어디쯤인지 모르면 멈춘 것처럼 보인다.
  // 진행 중 표시는 **어느 보고서의 작업인지**까지 들고 있어야 한다. 예전에는 참/거짓
  // 하나여서, 재생성 중에 다른 보고서로 옮기면 그쪽 버튼까지 잠겼다. 게다가 요청이
  // 끝나지 않으면(LLM 게이트웨이가 응답을 안 주는 경우) 영영 잠긴 채로 남았다.
  const [pdfStageId, setPdfStageId] = useState<string | null>(null);
  // 「요약 재생성」은 PDF 흐름과 별개다. 같은 상태를 쓰면 버튼 문구가 서로 바뀐다.
  const [regeneratingId, setRegeneratingId] = useState<string | null>(null);
  // 재생성이 끝나도 화면에 아무 변화가 없으면 됐는지 알 수 없다. 한 줄로 알린다.
  const [regenerated, setRegenerated] = useState(false);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const [calendarMonth, setCalendarMonth] = useState(() => {
    const date = defaultDate ? new Date(`${defaultDate}T00:00:00`) : new Date();
    return new Date(date.getFullYear(), date.getMonth(), 1);
  });
  // 첫 진입에서만 최신 보고서를 골라 둔다.
  //
  // 예전에는 `selectedDate` 가 이 effect 의 의존성이라, 사용자가 달력에서 날짜를
  // 누를 때마다(그때 선택을 비운다) 이 effect 가 다시 돌아 **최신 보고서를 곧바로
  // 되살렸다.** 그래서 다른 날짜를 골라도 이전 보고서의 결함 목록이 그대로 남았다.
  // 한 번만 심도록 ref 로 잠근다.
  const seededSelection = useRef(false);
  useEffect(() => {
    if (!defaultDate || seededSelection.current) return;
    seededSelection.current = true;
    setSelectedDate((current) => current || defaultDate);
    setSelectedReportId((current) => current || (initialReport ? String(initialReport.id) : ''));
    setCalendarMonth((current) => selectedDate ? current : (() => {
      const date = new Date(`${defaultDate}T00:00:00`);
      return new Date(date.getFullYear(), date.getMonth(), 1);
    })());
  }, [defaultDate, initialReport?.id, selectedDate]);
  // 같은 날짜에 여러 건이면 번호가 빠른 것이 위로 온다. 발번이 시간 순서라
  // 「먼저 만든 보고서가 위」가 된다. 번호가 없는 옛 문서는 id 로 갈음한다.
  const reportsForDate = reports
    .filter((item) => item.inspection_date === selectedDate)
    .sort((left, right) => {
      const key = (item: CloudReport) => item.report_no?.replace(/^R-/, '') ?? String(item.id).padStart(12, '0');
      return key(left).localeCompare(key(right));
    });
  const report = reports.find((item) => String(item.id) === selectedReportId) ?? null;
  // 지금 고른 보고서의 작업일 때만 버튼을 잠근다. 다른 보고서를 재생성하던 중이면
  // 이 보고서는 그대로 열 수 있어야 한다.
  const regenerating = report !== null && regeneratingId === String(report.id);
  const renderingPdf = report !== null && pdfStageId === String(report.id);
  const [reportDefects, setReportDefects] = useState<Defect[]>([]);
  useEffect(() => {
    if (!report) {
      setReportDefects([]);
      return;
    }
    const controller = new AbortController();
    fetch(`${API_BASE_URL}/sessions/${String(report.session_id)}/defects`, { cache: 'no-store', signal: controller.signal })
      .then((response) => response.ok ? response.json() : Promise.reject())
      .then((rows) => { if (!controller.signal.aborted) setReportDefects(mapDefectRows(rows)); })
      .catch(() => {});
    return () => controller.abort();
  }, [report]);
  // 검사 시간은 **그 보고서의** 점검 시각이어야 한다. 화면 위쪽에서 내려오는
  // `session` 은 지금 진행 중인 점검이라, 과거 보고서를 골라도 현재 세션의 시각이
  // 찍혀 있었다. 보고서가 가리키는 세션을 따로 읽는다.
  const [reportSession, setReportSession] = useState<CloudSession | null>(null);
  useEffect(() => {
    if (!report) {
      setReportSession(null);
      return;
    }
    const controller = new AbortController();
    fetch(`${API_BASE_URL}/sessions/${String(report.session_id)}`, { cache: 'no-store', signal: controller.signal })
      .then((response) => response.ok ? response.json() : Promise.reject())
      .then((row) => { if (!controller.signal.aborted) setReportSession(row as CloudSession); })
      .catch(() => { if (!controller.signal.aborted) setReportSession(null); });
    return () => controller.abort();
  }, [report]);
  const reportDates = new Set(reports.flatMap((item) => item.inspection_date ? [item.inspection_date] : []));
  const calendarYear = calendarMonth.getFullYear();
  const calendarMonthIndex = calendarMonth.getMonth();
  const leadingDays = new Date(calendarYear, calendarMonthIndex, 1).getDay();
  const daysInMonth = new Date(calendarYear, calendarMonthIndex + 1, 0).getDate();
  const calendarDays = Array.from({ length: leadingDays + daysInMonth }, (_, index) => index < leadingDays ? null : index - leadingDays + 1);
  const calendarDateKey = (day: number) => `${calendarYear}-${String(calendarMonthIndex + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
  const monthReportDays = [...reportDates].filter((date) => date.startsWith(`${calendarYear}-${String(calendarMonthIndex + 1).padStart(2, '0')}`)).length;
  // 「보고서 열기」 — 저장된 요약으로 HTML 을 띄우고, 이어서 PDF 를 받는다.
  //
  // 최초 생성은 점검 종료 팝업이 이미 해 두었으므로 여기서 LLM 을 다시 돌리지 않는다.
  // 저장된 요약을 쓰면 HTML 이 0.07초에 뜨고, 시간이 걸리는 PDF(사진 5장 기준 약 1분)는
  // 그 뒤에서 돈다 — 읽을 것은 이미 화면에 있으니 기다림이 체감되지 않는다.
  //
  // 새 탭은 **클릭 처리 중에** 연다. await 를 건너온 뒤에 열면 팝업 차단에 막힌다.
  const openReport = async () => {
    if (!report || renderingPdf) return;
    const reportId = String(report.id);
    const documentUrl = `${API_BASE_URL}/reports/${report.id}/document`;
    window.open(documentUrl, '_blank');

    setPdfError(null);
    setRegenerated(false);
    setPdfStageId(reportId);
    try {
      const response = await fetch(`${documentUrl}.pdf`, { signal: AbortSignal.timeout(REPORT_JOB_TIMEOUT_MS) });
      if (!response.ok) throw new Error('PDF 렌더링에 실패했습니다.');
      const blobUrl = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = blobUrl;
      link.download = `${report.report_no?.replace(/^R-/, '') ?? `report-${report.id}`}.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(blobUrl);
    } catch (error) {
      // HTML 은 이미 떠 있다. PDF 만 실패했다는 것을 알린다.
      setPdfError(reportJobErrorMessage(error, 'PDF 렌더링'));
    } finally {
      setPdfStageId((current) => current === reportId ? null : current);
    }
  };

  // 「요약 재생성」 — LLM 을 다시 돌려 문장을 새로 뽑아 저장한다. 판독이 실패했거나
  // 문장이 마음에 안 들 때 쓴다.
  //
  // 끝나고 보고서를 **자동으로 열지 않는다.** 새 탭은 사용자가 방금 클릭했을 때만
  // 열 수 있는데(transient activation), 1분짜리 요청을 기다리는 사이 그 자격이 사라진다.
  // 코드로 버튼을 눌러도 마찬가지다 — 자동 클릭은 이 자격을 만들어 주지 않는다.
  // 미리 빈 탭을 잡아 두면 자동으로 열 수 있지만 1분간 빈 화면이 떠 있게 된다.
  // 그래서 여기서는 끝났다고 알리고, 여는 것은 사용자가 「보고서 열기」로 한다.
  const regenerateSummary = async () => {
    if (!report || regenerating || renderingPdf) return;
    const reportId = String(report.id);
    setPdfError(null);
    setRegenerated(false);
    setRegeneratingId(reportId);
    try {
      const response = await fetch(
        `${API_BASE_URL}/reports/${report.id}/document?refresh=true`,
        { signal: AbortSignal.timeout(REPORT_JOB_TIMEOUT_MS) },
      );
      if (!response.ok) throw new Error('요약 재생성에 실패했습니다.');
      setRegenerated(true);
    } catch (error) {
      setPdfError(reportJobErrorMessage(error, '요약 재생성'));
    } finally {
      setRegeneratingId((current) => current === reportId ? null : current);
    }
  };

  const defectTypeCountMap = reportDefects.reduce<Record<string, number>>((counts, defect) => {
    const label = defectTypeLabel(defect.type);
    counts[label] = (counts[label] ?? 0) + 1;
    return counts;
  }, {});
  // 대시보드 「결함 통계」와 순서·색을 맞춘다. 같은 세 유형을 탭마다 다르게 늘어놓으면
  // 색을 매번 다시 읽어야 한다.
  const defectTypeCounts = ['균열', '막힘', '표면 손상'].map((type) => [type, defectTypeCountMap[type] ?? 0] as const);
  const reportDefectColor = (type: string) => type === '균열' ? '#6478f3' : type === '막힘' ? '#845cf5' : type === '표면 손상' ? SURFACE_DAMAGE_COLOR : '#8a94a6';
  const startedAtMs = parseUtcTimestamp(reportSession?.started_at);
  const endedAtMs = parseUtcTimestamp(reportSession?.ended_at);
  const duration = Number.isFinite(startedAtMs) && Number.isFinite(endedAtMs)
    ? Math.max(0, endedAtMs - startedAtMs)
    : undefined;
  const durationLabel = !report
    ? '--:--:--'
    : report.duration ?? (duration === undefined ? '--:--:--' : new Date(duration).toISOString().slice(11, 19));
  // 서버는 UTC 를 `2026-08-03T01:40:00` 처럼 **표기 없이** 내려보낸다. 그대로
  // `new Date()` 에 넣으면 브라우저가 로컬 시각으로 읽어 9시간 어긋난 값이 찍혔다.
  // `parseUtcTimestamp` 로 UTC 임을 알려 준 뒤 로컬로 표시하면, 화면 우측 위
  // 시계와 같은 시간대가 된다.
  const formatInspectionTime = (milliseconds: number) => {
    if (!Number.isFinite(milliseconds)) return '--:--:--';
    return new Date(milliseconds).toLocaleTimeString('ko-KR', {
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    });
  };
  // 검사 시간을 시간축으로 보여 준다. 결함이 언제 나왔는지가 축 위에 찍히므로
  // 「12분 30초 걸렸다」에 더해 「초반에 몰렸다 / 고르게 나왔다」까지 읽힌다.
  //
  // 시작·종료를 모르면 눈금을 찍을 기준이 없다. 그럴 때는 축을 비우고 시각만 적는다.
  const timelineSpan = Number.isFinite(startedAtMs) && Number.isFinite(endedAtMs)
    ? Math.max(1, endedAtMs - startedAtMs)
    : 0;
  const timelineMarks = timelineSpan
    ? reportDefects
        .filter((defect) => defect.detectedAtMs !== undefined)
        .map((defect) => ({
          id: defect.id,
          severity: defect.severity,
          // 종료 뒤에 찍힌 결함이 있어도 축 밖으로 나가지 않게 가둔다.
          ratio: Math.min(1, Math.max(0, ((defect.detectedAtMs as number) - startedAtMs) / timelineSpan)),
          label: `${defect.time} · ${defectTypeLabel(defect.type)} · ${severityLabel[defect.severity]}`,
        }))
    : [];
  return (
    <section className="report-page">
      <aside className="three-d-picker report-picker">
        <div className="three-d-picker-block">
          <header className="three-d-picker-head">
            <span className="three-d-picker-title"><CalendarDays size={16} />점검 날짜</span>
            <span className="three-d-picker-note">{monthReportDays > 0 ? `이 달 기록 ${monthReportDays}일` : '이 달 기록 없음'}</span>
          </header>
          <div className="three-d-calendar">
            <div className="report-calendar-header">
              <button type="button" aria-label="이전 달" onClick={() => setCalendarMonth(new Date(calendarYear, calendarMonthIndex - 1, 1))}><ArrowLeft size={16} /></button>
              <strong>{calendarYear}년 {calendarMonthIndex + 1}월</strong>
              <button type="button" aria-label="다음 달" onClick={() => setCalendarMonth(new Date(calendarYear, calendarMonthIndex + 1, 1))}><ArrowRight size={16} /></button>
            </div>
            <div className="report-calendar-weekdays">{['일', '월', '화', '수', '목', '금', '토'].map((day) => <span key={day}>{day}</span>)}</div>
            <div className="report-calendar-days">
              {calendarDays.map((day, index) => day === null ? <i key={`blank-${index}`} /> : (() => {
                const dateKey = calendarDateKey(day);
                const hasReport = reportDates.has(dateKey);
                return <button type="button" key={dateKey} aria-pressed={selectedDate === dateKey} className={`${hasReport ? 'has-report' : ''} ${selectedDate === dateKey ? 'selected' : ''}`} onClick={() => { setSelectedDate(dateKey); setSelectedReportId(''); }}>{day}</button>;
              })())}
            </div>
          </div>
          <p className="three-d-calendar-legend"><i className="has-report" />점검 기록 있는 날</p>
        </div>
        <div className="three-d-picker-block">
          <header className="three-d-picker-head">
            {/* 고른 날짜는 달력에 이미 칠해져 있어 여기 또 적을 필요가 없다. */}
            <span className="three-d-picker-title">보고서</span>
          </header>
          {reportsForDate.length === 0 ? (
            <p className="three-d-empty">{selectedDate ? '이 날짜에는 점검 보고서가 없습니다.' : '달력에서 날짜를 고르세요.'}</p>
          ) : (
            <ul className="three-d-report-list">
              {reportsForDate.map((item) => {
                const id = String(item.id);
                // 보고서를 바꾸면 이전 보고서에 대한 안내·오류는 더 이상 맞지 않는다.
                const select = () => { setSelectedReportId(id); setRegenerated(false); setPdfError(null); };
                return <li key={id}><button type="button" aria-pressed={selectedReportId === id} className={selectedReportId === id ? 'selected' : ''} onClick={select}><strong>{item.report_no?.replace(/^R-/, '') ?? `보고서 ${item.id}`}</strong><span>{item.region ?? `세션 ${item.session_id}`}</span></button></li>;
              })}
            </ul>
          )}
        </div>

        {/* 최초 생성은 점검 종료 팝업이 이미 했다. 여기서는 열어 보거나 다시 뽑는다.
            두 버튼을 한 줄에 나란히 두어 남는 세로를 보고서 목록에 넘긴다. */}
        <div className="report-picker-actions">
          <button className="primary-button three-d-generate" disabled={!report || regenerating || renderingPdf} onClick={() => void openReport()}>{renderingPdf ? 'PDF 렌더링 중…' : '보고서 열기'}</button>
          {/* 시연 중에는 잠가 둔다. 누구든 누를 수 있는데 한 번에 LLM 호출이 나가고
              수십 초가 걸려, 여러 사람이 연달아 누르면 게이트웨이가 밀린다.
              시연이 끝나면 SUMMARY_REGENERATION_ENABLED 만 true 로 돌리면 된다. */}
          <button type="button" className="outline-button three-d-generate" disabled={!SUMMARY_REGENERATION_ENABLED || !report || regenerating || renderingPdf} title={SUMMARY_REGENERATION_ENABLED ? undefined : '시연 중에는 사용할 수 없습니다.'} onClick={() => void regenerateSummary()}>{regenerating ? '재생성 중…' : '요약 재생성'}</button>
        </div>
        {regenerated && <p className="three-d-generate-hint" role="status">요약을 새로 만들었습니다. 「보고서 열기」로 확인하세요.</p>}
        {pdfError && <p className="report-picker-error" role="alert">{pdfError}</p>}
      </aside>

      <div className="report-page-content">
        <div className="report-kpis"><article className="card"><div className="report-kpi-heading"><span>전체 결함</span><span>{report?.total_defect_count ?? reportDefects.length}건</span></div><div className="report-defect-counts">{defectTypeCounts.map(([type, count]) => <div key={type}><i className="report-defect-count-dot" aria-hidden="true" style={{ background: reportDefectColor(type) }} /><b>{type}</b><span className="report-defect-count-bar"><i style={{ width: `${reportDefects.length ? count / reportDefects.length * 100 : 0}%`, background: reportDefectColor(type) }} /></span><b>{count}건</b></div>)}</div></article><article className="card report-time-card"><span className="report-time-heading">검사 시간<em>{durationLabel}</em></span><div className="report-timeline"><div className="report-timeline-track"><i className="report-timeline-fill" />{timelineMarks.map((mark) => <i key={mark.id} className={`report-timeline-mark ${mark.severity.toLowerCase()}`} style={{ left: `${mark.ratio * 100}%` }} title={mark.label} />)}</div><div className="report-timeline-ends"><b>{formatInspectionTime(startedAtMs)}<small>시작</small></b><b>{formatInspectionTime(endedAtMs)}<small>종료</small></b></div></div></article></div>
        <ReportDefectList defects={reportDefects} report={report} selected={Boolean(selectedReportId)} />
      </div>
    </section>
  );
}

class RemoteCommandRejectedError extends Error {}

export default function App() {
  const [page, setPage] = useSessionState<Page>('pipecheck.current-page', 'priority');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [defects, setDefects] = useState<Defect[]>([]);
  const [session, setSession] = useState<CloudSession | null>(null);
  const [report, setReport] = useState<CloudReport | null>(null);
  const [reports, setReports] = useState<CloudReport[]>([]);
  const [robot, setRobot] = useState<CloudRobot | null>(null);
  const [operator, setOperator] = useState<CloudOperator | null>(null);
  const [availableRobots, setAvailableRobots] = useState<CloudRobot[]>([]);
  const [availableOperators, setAvailableOperators] = useState<CloudOperator[]>([]);
  const [inspectionSetupOpen, setInspectionSetupOpen] = useState(false);
  const [emergencyAlertOpen, setEmergencyAlertOpen] = useState(false);
  const [inspectionSetup, setInspectionSetup] = useState<InspectionSetupValues>(createInspectionSetupValues);
  const [configuredInspection, setConfiguredInspection] = useSessionState<InspectionSetupValues | null>('pipecheck.configured-inspection', null);
  const [completionRunningIdentity, setCompletionRunningIdentity] = useSessionState<string | null>(
    'pipecheck.completion-running-identity',
    null,
  );
  // 점검 종료 후 뜨는 결과 팝업. 여기서만 보고서 생성과 새 점검으로 갈라진다.
  const [inspectionResultOpen, setInspectionResultOpen] = useState(false);
  const [endingInspection, setEndingInspection] = useState(false);
  const [serverLive, setServerLive] = useState<LiveData>({});
  const [agentLive, setAgentLive] = useState<LiveData>({});
  const [agentConnected, setAgentConnected] = useState(false);
  const [agentIdentity, setAgentIdentity] = useState<{ sessionId?: string | number; operationId?: string }>({});
  const [serverEvents, setServerEvents] = useState<RobotEvent[]>([]);
  const [aiSummary, setAiSummary] = useState<AiSummaryData>(idleAiSummary);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (
      configuredInspection
      && (configuredInspection.sessionId === undefined || configuredInspection.operationId === undefined)
    ) {
      setConfiguredInspection(null);
    }
  }, [configuredInspection, setConfiguredInspection]);
  // 백엔드(cloud_api)에 닿지 못하는 상태. 배너로 표시합니다.
  const [backendDown, setBackendDown] = useState(false);
  // Agent 연속 실패 횟수. 폴링 간격을 늘리는 데 씁니다.
  const agentFailuresRef = useRef(0);
  const emergencyAlertShownRef = useRef(false);
  const inspectionGenerationRef = useRef(0);
  const inspectionIdentityRef = useRef('none');
  const cloudRequestRef = useRef(0);
  const agentRequestRef = useRef(0);
  const inspectionIdentity = configuredInspection
    ? `${String(configuredInspection.sessionId ?? 'pending')}\u0000${String(configuredInspection.operationId ?? '')}`
    : 'none';
  if (inspectionIdentityRef.current !== inspectionIdentity) {
    inspectionIdentityRef.current = inspectionIdentity;
    inspectionGenerationRef.current += 1;
  }

  const live = useMemo(() => {
    // 활성 검사에서는 과거 Cloud session/drive-log 값을 live 센서값으로 재사용하지 않는다.
    // Agent telemetry가 끊기면 빈 상태를 보여 stale 자세·거리를 현재값처럼 노출하지 않는다.
    const merged: LiveData = configuredInspection ? {} : { ...serverLive };
    for (const [key, value] of Object.entries(agentLive)) {
      if (value !== undefined && value !== null && value !== '') {
        (merged as Record<string, unknown>)[key] = value;
      }
    }
    return merged;
  }, [configuredInspection, serverLive, agentLive]);
  const events = serverEvents;

  const loadCloudData = useCallback(async () => {
    const generation = inspectionGenerationRef.current;
    const requestId = ++cloudRequestRef.current;
    const requestIsCurrent = () => (
      generation === inspectionGenerationRef.current && requestId === cloudRequestRef.current
    );
    try {
      const [sessionResponse, robotResponse, operatorResponse, reportResponse] = await Promise.all([
        fetch(`${API_BASE_URL}/sessions`, { cache: 'no-store' }),
        fetch(`${API_BASE_URL}/robots`, { cache: 'no-store' }),
        fetch(`${API_BASE_URL}/operators`, { cache: 'no-store' }),
        fetch(`${API_BASE_URL}/reports`, { cache: 'no-store' }),
      ]);
      if (!requestIsCurrent()) return;
      if (!sessionResponse.ok) {
        // 비 2xx(대개 500)도 백엔드 문제입니다. 조용히 돌아가면 배너 표시·해제
        // 경로를 통째로 건너뛰어, 가장 흔한 장애에서 배너가 안 뜹니다.
        console.error('[backend] 세션 조회 실패:', sessionResponse.status);
        setBackendDown(true);
        return;
      }
      const sessions = asArray(await sessionResponse.json());
      const robots = robotResponse.ok ? asArray(await robotResponse.json()) as unknown as CloudRobot[] : [];
      const operators = operatorResponse.ok ? asArray(await operatorResponse.json()) as unknown as CloudOperator[] : [];
      const availableReports = reportResponse.ok ? asArray(await reportResponse.json()) as unknown as CloudReport[] : [];
      setAvailableRobots(robots);
      setAvailableOperators(operators);
      setReports(availableReports);
      const current = (configuredInspection?.sessionId !== undefined
        ? sessions.find((candidate) => String(candidate.id) === String(configuredInspection.sessionId))
        : sessions[0]) as CloudSession | undefined;
      setSession(current ?? null);
      if (!current) {
        setDefects([]);
        setReport(null);
        setRobot(null);
        setOperator(null);
        setServerLive({});
        setServerEvents([]);
        return;
      }

      const [defectResult, driveLogResult] = await Promise.allSettled([
        fetch(`${API_BASE_URL}/sessions/${String(current.id)}/defects`, { cache: 'no-store' }).then((response) => response.ok ? response.json() : Promise.reject()),
        fetch(`${API_BASE_URL}/sessions/${String(current.id)}/drive-logs`, { cache: 'no-store' }).then((response) => response.ok ? response.json() : Promise.reject()),
      ]);
      if (!requestIsCurrent()) return;

      const rows = asArray(defectResult.status === 'fulfilled' ? defectResult.value : []);
      const mappedDefects = mapDefectRows(rows);
      setDefects(mappedDefects);

      setReport(availableReports.find((item) => String(item.session_id) === String(current.id)) ?? null);

      const currentRobot = robots.find((item) => String(item.id) === String(current.robot_id)) ?? null;
      setRobot(currentRobot);

      setOperator(operators.find((item) => String(item.id) === String(current.approved_by)) ?? null);

      const driveLogs = asArray(driveLogResult.status === 'fulfilled' ? driveLogResult.value : []);
      const latestLog = driveLogs[0] ?? {};
      const imu = asRecord(latestLog.imu_reading ?? latestLog.imuReading);
      const confidenceValues = mappedDefects.flatMap((item) => item.confidence === undefined ? [] : [item.confidence]);
      const finalPosition = rows.reduce((maximum, row) => Math.max(maximum, numericValue(row.approx_position_m, row.distance_m) ?? 0), 0);
      setServerLive({
        roll: numericValue(imu.roll),
        pitch: numericValue(imu.pitch),
        yaw: numericValue(imu.yaw),
        tilt: numericValue(imu.pitch),
        distance: finalPosition || undefined,
        missionState: textValue(current.status, latestLog.agent_state),
        section: current.district_id === undefined ? `세션 ${current.id}` : `검사 구역 ${current.district_id}`,
        detectionConfidence: confidenceValues.length ? confidenceValues.reduce((sum, value) => sum + value, 0) / confidenceValues.length : undefined,
      });
      setServerEvents(mapDriveLogRows(driveLogs));
    } catch (error) {
      if (!requestIsCurrent()) return;
      // 예전에는 조용히 삼켰습니다. 그러면 "데이터가 없는 것"과 "서버가 죽은 것"을
      // 구분할 수 없어, 빈 목록만 보고 원인을 짐작해야 했습니다.
      console.error('[backend] 세션·보고서 조회 실패:', error);
      setBackendDown(true);
      return;
    }
    setBackendDown(false);
  }, [configuredInspection]);

  const loadAgentData = useCallback(async () => {
    const generation = inspectionGenerationRef.current;
    const requestId = ++agentRequestRef.current;
    const requestIsCurrent = () => (
      generation === inspectionGenerationRef.current && requestId === agentRequestRef.current
    );
    try {
      const response = await fetch(`${API_BASE_URL}/agent/telemetry`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`Cloud Agent telemetry ${response.status}`);
      const telemetry = asRecord(await response.json());
      if (!requestIsCurrent()) return;
      const telemetryUpdatedAt = parseUtcTimestamp(textValue(telemetry.updatedAt, telemetry.updated_at));
      if (!Number.isFinite(telemetryUpdatedAt) || Date.now() - telemetryUpdatedAt > 15_000) {
        setAgentConnected(false);
        setAgentLive({});
        setAgentIdentity({});
        setAiSummary(idleAiSummary());
        return;
      }
      const telemetrySessionId = telemetry.sessionId ?? telemetry.session_id;
      const telemetryOperationId = telemetry.operationId ?? telemetry.operation_id;
      const expectedSessionId = configuredInspection?.sessionId;
      const expectedOperationId = configuredInspection?.operationId;
      const telemetryMatchesInspection = !configuredInspection || (
        expectedSessionId !== undefined
        && telemetrySessionId !== undefined
        && String(telemetrySessionId) === String(expectedSessionId)
        && expectedOperationId !== undefined
        && telemetryOperationId !== undefined
        && String(telemetryOperationId) === String(expectedOperationId)
      );
      if (!telemetryMatchesInspection) {
        setAgentConnected(false);
        setAgentLive({});
        setAgentIdentity({});
        setAiSummary(idleAiSummary());
        return;
      }
      setAgentIdentity({ sessionId: telemetrySessionId, operationId: telemetryOperationId });
      const mission = asRecord(telemetry.mission);
      const hermes = asRecord(telemetry.hermes);
      const drive = asRecord(telemetry.drive);
      const summary = asRecord(telemetry.qwenSummary ?? telemetry.qwen_summary);
      setAgentConnected(true);

      const observation = asRecord(drive.latestObservation ?? drive.latest_observation);
      const command = asRecord(drive.latestCommand ?? drive.latest_command);
      const imu = asRecord(observation.imuReading ?? observation.imu_reading ?? drive.imu);
      const motorOutput = asRecord(drive.motorOutput ?? drive.motor_output);
      const motorLeft = numericValue(motorOutput.left, command.leftSpeed, command.left_speed);
      const motorRight = numericValue(motorOutput.right, command.rightSpeed, command.right_speed);
      const result = asRecord(drive.latestPerception ?? drive.latest_perception);
      const summaryStatus = textValue(summary.status)?.toLowerCase();
      const summaryFailed = summaryStatus === 'error' || summaryStatus === 'unavailable';
      const generatedAt = textValue(summary.generatedAt, summary.generated_at, summary.updatedAt, summary.updated_at);
      const rawSummaryMessage = textValue(summary.message, summary.summary);
      const summaryMessage = rawSummaryMessage?.trim().split(/\r?\n/, 1)[0].slice(0, 20);
      // **지난 점검의 요약을 이번 점검에 띄우지 않는다.**
      //
      // 점검을 시작할 때 요약을 비우지만(`submitInspectionSetup`), 바로 다음 폴링에서
      // Agent 가 아직 들고 있는 **예전 요약**이 그 자리를 다시 채운다. 실제로 04:06 에
      // 시작한 점검 화면에 03:30 요약이 떠 있었다.
      //
      // 그래서 생성 시각을 본다. 이번 점검을 설정한 시각보다 앞서면 지난 점검 것이고,
      // 그런 기준이 없더라도 너무 오래된 것은 지금 상황이 아니다.
      const generatedAtMs = generatedAt ? new Date(generatedAt).getTime() : Number.NaN;
      const inspectionStartedMs = configuredInspection?.configuredAt
        ? new Date(configuredInspection.configuredAt).getTime()
        : Number.NaN;
      const summaryIsStale = Number.isFinite(generatedAtMs) && (
        (Number.isFinite(inspectionStartedMs) && generatedAtMs < inspectionStartedMs)
        || Date.now() - generatedAtMs > SUMMARY_STALE_AFTER_MS
      );
      if (summaryIsStale) {
        setAiSummary(idleAiSummary());
      } else if (!summaryFailed) {
        const tone: AiSummaryData['tone'] = summaryStatus === 'ready'
          ? 'ready'
          : summaryStatus === 'pending'
            ? 'pending'
            : summaryMessage
              ? 'ready'
              : 'idle';
        setAiSummary({
          message: summaryMessage ?? (tone === 'pending' ? '상황 분석 중' : '요약 대기 중'),
          time: generatedAt ? new Date(generatedAt).toLocaleTimeString('en-GB', { hour12: false }) : '--:--:--',
          model: textValue(summary.model, summary.modelName, summary.model_name) ?? 'Qwen2.5-VL',
          tone,
        });
      }

      const hermesState = textValue(hermes.state)?.toUpperCase();
      const missionState = textValue(mission.state)?.toUpperCase();
      const explicitRunningIdentity = explicitRunningInspectionIdentity({
        expectedSessionId,
        expectedOperationId,
        telemetrySessionId,
        telemetryOperationId,
        hermesState,
        missionState,
      });
      if (explicitRunningIdentity) setCompletionRunningIdentity(explicitRunningIdentity);
      const cloudSessionState = textValue(session?.status)?.toUpperCase();
      const reportedState = hermesState === 'COMPLETED' || missionState === 'COMPLETED'
        ? 'COMPLETED'
        : hermesState === 'STOPPED' || missionState === 'STOPPED'
          ? 'PAUSED'
          : ['ERROR', 'FAILED', 'BLOCKED', 'ABORTED'].includes(hermesState ?? '')
              || ['ERROR', 'FAILED', 'BLOCKED', 'ABORTED'].includes(missionState ?? '')
            ? 'ERROR'
            : hermesState ?? missionState;
      const currentInspectionState = cloudSessionState === 'COMPLETED' || cloudSessionState === 'ABORTED'
        ? cloudSessionState
        : configuredInspection
          ? reportedState ?? 'RUNNING'
          : reportedState;
      const nextAgentLive: LiveData = {
        speed: motorLeft === undefined && motorRight === undefined
          ? undefined
          : motorOutputPercent({ left: motorLeft ?? 0, right: motorRight ?? 0 }),
        tilt: numericValue(imu.pitchDeg, imu.pitch_deg, observation.inclineDegrees, observation.pitchDegrees, imu.pitch),
        roll: numericValue(imu.rollDeg, imu.roll_deg, observation.roll, imu.roll),
        pitch: numericValue(imu.pitchDeg, imu.pitch_deg, observation.pitchDegrees, observation.pitch, imu.pitch),
        yaw: numericValue(imu.yawDeg, imu.yaw_deg, observation.yaw, imu.yaw),
        distance: numericValue(mission.distanceM, mission.distance_m, drive.distanceM),
        temperature: numericValue(drive.temperatureC, drive.temperature_c, mission.temperatureC),
        latency: numericValue(drive.latencyMs, drive.latency_ms),
        voltage: numericValue(drive.voltage, mission.voltage),
        operatingMinutes: numericValue(mission.remainingOperatingMinutes, mission.remaining_minutes),
        missionState: currentInspectionState,
        section: textValue(mission.area, session?.area, session?.region, session?.district_id === undefined ? undefined : `검사 구역 ${session.district_id}`),
        detectionConfidence: toPercent(numericValue(result.confidence)),
        inspectionProgress: toPercent(numericValue(mission.progress, mission.progressPercent, mission.progress_percent)),
      };
      setAgentLive(Object.fromEntries(
        Object.entries(nextAgentLive).filter(([, value]) => value !== undefined && value !== null),
      ) as LiveData);
      agentFailuresRef.current = 0;
    } catch (error) {
      if (!requestIsCurrent()) return;
      setAgentConnected(false);
      setAgentLive({});
      setAgentIdentity({});
      setAiSummary(idleAiSummary());
      agentFailuresRef.current += 1;
      if (agentFailuresRef.current === 1) {
        console.warn('[cloud-agent] EC2 telemetry 연결 실패 — 폴링 간격을 늘립니다.', error);
      }
    }
  }, [configuredInspection, session?.area, session?.region, session?.district_id, session?.status, setCompletionRunningIdentity]);

  useEffect(() => {
    void loadCloudData();
  }, [loadCloudData]);

  // 클라우드 데이터를 주기적으로 다시 읽는다. 결함·보고서·세션이 여기서 갱신되므로,
  // 이 폴링이 없으면 AI 가 결함을 올려도 새로고침해야 화면에 보인다.
  //
  // 주기를 상황에 맞춰 나눈다. `loadCloudData` 는 요청 6개를 보내므로(세션·로봇·
  // 운영자·보고서·결함·주행로그) 짧게 돌리면 그만큼 부담이 커진다.
  //
  //   점검 중   5초   — 결함이 실시간으로 쌓이는 구간
  //   그 외    20초   — 보고서 목록 정도만 바뀐다
  //   장애 중  30초   — 복구 확인용. 배너가 뜬 뒤 이 재시도가 없으면 백엔드가
  //                     살아나도 배너가 영영 남는다
  useEffect(() => {
    const state = live.missionState?.toUpperCase() ?? '';
    const inspecting = ['RUNNING', 'PAUSED', 'RETURNING', 'DRIVING'].includes(state);
    const period = backendDown ? 30_000 : inspecting ? 5_000 : 20_000;
    const interval = window.setInterval(() => void loadCloudData(), period);
    return () => window.clearInterval(interval);
  }, [backendDown, live.missionState, loadCloudData]);

  useEffect(() => {
    void loadAgentData();
    // 2초마다 깨어나되, 연속 실패가 쌓이면 그만큼 건너뜁니다
    // (2s → 4 → 8 → 16 → 최대 32s). 성공하면 카운터가 0 이 되어 즉시 2초로 복귀합니다.
    let skip = 0;
    const interval = window.setInterval(() => {
      if (skip > 0) {
        skip -= 1;
        return;
      }
      void loadAgentData();
      const failures = agentFailuresRef.current;
      skip = failures === 0 ? 0 : Math.min(2 ** failures, 16) - 1;
    }, 2000);
    return () => {
      window.clearInterval(interval);
    };
  }, [loadAgentData]);

  const robotState: RobotState = live.missionState === 'RUNNING'
    ? 'running'
    : live.missionState === 'PAUSED'
      ? 'paused'
      : live.missionState === 'RETURNING'
        ? 'returning'
        : live.missionState === 'EMERGENCY_STOP'
          ? 'emergency'
          : 'ready';

  useEffect(() => {
    const emergencyActive = live.missionState?.toUpperCase() === 'EMERGENCY_STOP';
    if (emergencyActive && !emergencyAlertShownRef.current) {
      emergencyAlertShownRef.current = true;
      setEmergencyAlertOpen(true);
      return;
    }
    if (!emergencyActive) emergencyAlertShownRef.current = false;
  }, [live.missionState]);

  const sendRemoteHermesCommand = useCallback(async (
    commandType: 'START' | 'STOP',
    inspectionArea?: string,
    sessionId?: string | number,
    operationId?: string,
  ) => {
    const numericSessionId = Number(sessionId);
    const normalizedArea = textValue(inspectionArea, session?.area, session?.region);
    if (!Number.isInteger(numericSessionId) || numericSessionId <= 0 || !operationId || !normalizedArea) {
      throw new Error(`원격 ${commandType} identity가 올바르지 않습니다.`);
    }
    const response = await fetch(`${API_BASE_URL}/agent/commands`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        command_type: commandType,
        agent_id: 'laptop-4070',
        request_id: crypto.randomUUID(),
        area: normalizedArea,
        session_id: numericSessionId,
        operation_id: operationId,
        expires_in_s: 30,
      }),
    });
    const command = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(command?.detail || `원격 ${commandType} command 등록 실패`);
    }

    for (let attempt = 0; attempt < 24; attempt += 1) {
      const statusResponse = await fetch(`${API_BASE_URL}/agent/commands/${String(command.command_id)}`, {
        cache: 'no-store',
      });
      const statusPayload = await statusResponse.json().catch(() => null);
      if (!statusResponse.ok) throw new Error(statusPayload?.detail || `원격 ${commandType} 상태 확인 실패`);
      if (statusPayload?.status === 'SUCCEEDED') return true;
      if (statusPayload?.status === 'FAILED') {
        throw new RemoteCommandRejectedError(statusPayload?.message || `노트북 Agent가 ${commandType}를 거부했습니다.`);
      }
      await new Promise((resolve) => window.setTimeout(resolve, 500));
    }
    throw new Error(`노트북 Agent의 ${commandType} 확인 시간이 초과되었습니다.`);
  }, [session?.area, session?.region]);

  const handleRobotAction = useCallback(async (requestedState: RobotState, inspectionArea?: string, sessionId?: string | number, operationId?: string, propagateFailure = false) => {
    if (requestedState !== 'running') {
      setNotice('현재 AI Agent API에서 지원하지 않는 명령입니다.');
      return false;
    }
    try {
      await sendRemoteHermesCommand('START', inspectionArea, sessionId, operationId);
      setNotice('원격 검사가 시작되었습니다.');
      return true;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '검사 시작 요청에 실패했습니다.');
      if (propagateFailure) throw error;
      return false;
    }
  }, [sendRemoteHermesCommand]);

  const stopHermesInspection = useCallback(async () => {
    try {
      await sendRemoteHermesCommand(
        'STOP',
        configuredInspection?.sectionName,
        configuredInspection?.sessionId ?? session?.id,
        configuredInspection?.operationId,
      );
      setNotice('점검을 일시정지했습니다.');
      void loadAgentData();
      return true;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '일시정지 요청에 실패했습니다.');
      return false;
    }
  }, [configuredInspection, loadAgentData, sendRemoteHermesCommand, session?.id]);

  // 재개는 **같은 세션·같은 operation** 으로 Agent 주행을 다시 건다. 세션을 새로
  // 만들지 않으므로 그동안 쌓인 결함과 주행 로그가 이어진다.
  //
  // 다만 Agent 에는 pause/resume 이 없고 start/stop 뿐이다. 그래서 로봇은 멈춘
  // 지점을 기억하지 못하고 주행 판단을 처음부터 다시 시작한다. 진짜 재개가
  // 필요하면 Agent 쪽에 재개 엔드포인트가 있어야 한다.
  const resumeHermesInspection = useCallback(async () => {
    if (configuredInspection?.sessionId === undefined) {
      setNotice('재개할 점검 정보가 없습니다. 점검을 새로 시작하세요.');
      return false;
    }
    const resumed = await handleRobotAction(
      'running',
      configuredInspection.sectionName,
      configuredInspection.sessionId,
      configuredInspection.operationId,
    );
    if (resumed) setNotice('점검을 재개했습니다.');
    return resumed;
  }, [configuredInspection, handleRobotAction]);

  const handleInspectionControl = useCallback(async (requestedState: RobotState) => {
    if (requestedState === 'paused') return stopHermesInspection();
    if (requestedState === 'running') return resumeHermesInspection();
    return false;
  }, [resumeHermesInspection, stopHermesInspection]);

  // Agent 미션 명령 하나를 보낸다. 실패하면 던져서 호출부가 화면 이동을 멈추게 한다 —
  // 초기화가 안 된 채 우선순위 화면으로 넘어가면 다음 점검이 옛 미션 위에서 시작된다.
  const postMission = useCallback(async (action: string) => {
    const response = await fetch(`${AGENT_API_URL}/api/mission/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => null);
      throw new Error(detail?.detail || `Agent ${action} 요청에 실패했습니다.`);
    }
    return response.json().catch(() => ({}));
  }, []);

  // 로봇 정지와 세션 종료를 **따로** 처리한다.
  //
  // 예전에는 `if (!stopped) throw` 로 정지에 실패하면 그 자리에서 빠져나갔다. 그래서
  // Agent 가 STOP 을 확인해 주지 못하면 세션이 `RUNNING` 인 채로 남았고, 그 세션이
  // 로봇을 잠가 다음 점검을 못 걸었다. 「안전 중지」 안내가 뜬 뒤 종료가 안 되던
  // 것이 이것이다.
  //
  // 운영자가 「점검 종료」를 눌렀으면 그 점검은 끝난 것이다. 로봇이 멈췄는지는 별개
  // 문제이므로 따로 알리고, 기록은 어느 쪽이든 닫는다.
  //
  // 모니터링의 긴급 정지(STOP)도 이 함수를 쓴다. 되돌릴 수 없는 조작이라 세션을
  // 열어 둘 이유가 없다 — 예전에는 일시정지와 같은 경로여서 세션이 RUNNING 으로
  // 남았고, 그 로봇으로 다음 점검을 걸 수 없었다.
  const handleEndInspection = useCallback(async () => {
    setEndingInspection(true);
    // PATCH 를 보내기 **전에** 표시한다. 완료가 기록된 뒤에 표시하면 그 사이에 폴링이
    // 먼저 눈치채, 끝낸 화면이 스스로를 구경꾼으로 오해한다.
    endedByThisBrowserRef.current = true;
    try {
      const stopped = await stopHermesInspection();
      if (session?.id === undefined) throw new Error('현재 점검 session 없음');

      // 종료 기록은 한 번 실패했다고 포기하면 세션이 영영 열린 채로 남는다.
      // 자동 완료 경로와 같은 방식으로 세 번까지 다시 건다.
      let closed: CloudSession | null = null;
      let lastError: Error | null = null;
      for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
          const response = await fetch(`${API_BASE_URL}/sessions/${String(session.id)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: 'COMPLETED' }),
          });
          if (!response.ok) throw new Error(`Cloud session 종료 기록 실패 (${response.status})`);
          closed = asRecord(await response.json()) as CloudSession;
          break;
        } catch (error) {
          lastError = error instanceof Error ? error : new Error('Cloud session 종료 기록 실패');
          if (attempt < 2) await new Promise((resolve) => window.setTimeout(resolve, 500 * (attempt + 1)));
        }
      }
      if (!closed) throw lastError ?? new Error('Cloud session 종료 기록 실패');

      setSession(closed);
      void loadAgentData();
      setInspectionResultOpen(true);
      if (!stopped) {
        setNotice('점검은 종료 처리했지만 로봇 정지 확인에 실패했습니다. 로봇 상태를 확인하세요.');
      }
    } catch (error) {
      setNotice(`점검 종료 실패: ${error instanceof Error ? error.message : '응답 없음'}`);
    } finally {
      setEndingInspection(false);
    }
  }, [loadAgentData, session?.id, stopHermesInspection]);

  // 로봇이 스스로 점검을 끝낸 경우에도 팝업을 띄운다.
  //
  // `handleEndInspection` 은 프론트에서 「점검 종료」를 누른 경우만 잡는다. Agent 가
  // 구간을 다 돌고 자체적으로 완료하면 프론트는 아무것도 모른 채 남는다. 미션 상태를
  // 2초마다 받아오므로 **활성 → 완료로 넘어가는 순간**을 보고 띄운다.
  //
  // 상태값이 아니라 **전이**를 봐야 한다. 완료 상태로 계속 머무르는 동안 매번 띄우면
  // 팝업을 닫을 수 없다. 그래서 직전 상태를 기억한다.
  const previousMissionStateRef = useRef<string | undefined>(undefined);
  const autonomousCompletionInFlightRef = useRef(false);
  // 이 화면에서 점검을 끝냈는지. 「끝났다」는 사실은 모든 화면에 똑같이 도착하지만
  // 결과 팝업은 **끝낸 사람에게만** 떠야 한다.
  const endedByThisBrowserRef = useRef(false);
  useEffect(() => {
    const current = live.missionState?.toUpperCase();
    previousMissionStateRef.current = current;
    const currentIdentity = inspectionIdentityKey(
      configuredInspection?.sessionId,
      configuredInspection?.operationId,
    );
    // 현재 session/operation의 원본 Agent telemetry에서 RUNNING을 실제 관측한 뒤의
    // COMPLETED만 인정한다. 관측 identity는 sessionStorage에 남겨 reload도 안전하게
    // 복구하되, 새 START의 다른 identity에는 절대 재사용하지 않는다.
    if (!shouldAcceptAutonomousCompletion({
      currentIdentity,
      runningObservedIdentity: completionRunningIdentity,
      missionState: current,
    })) return;
    if (session?.status?.toUpperCase() === 'COMPLETED') {
      // 이 화면에서 끝냈다면 팝업은 `handleEndInspection` 이 이미 띄웠다. 여기서는
      // 아무것도 하지 않는다 — `configuredInspection` 을 지우면 팝업의 구간명이
      // 사라진다.
      if (endedByThisBrowserRef.current) return;
      // 우리가 알아채기 **전에 이미** 완료로 기록돼 있었다면 끝낸 사람은 다른 화면이다.
      // 그쪽에 이미 팝업이 떠 있는데 여기서 또 띄우면 보던 화면을 가로챈다.
      // 이 탭은 조용히 손을 뗀다 — 대기 상태로 돌아간다.
      //
      // 같은 브라우저에서 탭을 복제하면 sessionStorage 가 함께 복사되어, 한 점검을
      // 두 탭이 자기 것으로 여기는 상황이 실제로 생긴다.
      setConfiguredInspection(null);
      return;
    }
    if (session?.id === undefined || autonomousCompletionInFlightRef.current) return;

    autonomousCompletionInFlightRef.current = true;
    const sessionId = session.id;
    void (async () => {
      try {
        let lastError: Error | null = null;
        for (let attempt = 0; attempt < 3; attempt += 1) {
          try {
            const response = await fetch(`${API_BASE_URL}/sessions/${String(sessionId)}`, {
              method: 'PATCH',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ status: 'COMPLETED' }),
            });
            if (!response.ok) throw new Error(`Cloud session 완료 기록 실패 (${response.status})`);
            setSession(asRecord(await response.json()) as CloudSession);
            setInspectionResultOpen(true);
            return;
          } catch (error) {
            lastError = error instanceof Error ? error : new Error('Cloud session 완료 기록 실패');
            if (attempt < 2) await new Promise((resolve) => window.setTimeout(resolve, 500 * (attempt + 1)));
          }
        }
        throw lastError ?? new Error('Cloud session 완료 기록 실패');
      } catch (error) {
        setNotice(`자동 점검 완료 기록 실패: ${error instanceof Error ? error.message : '응답 없음'}`);
      } finally {
        autonomousCompletionInFlightRef.current = false;
      }
    })();
  }, [completionRunningIdentity, live.missionState, configuredInspection, session?.id, session?.status, setConfiguredInspection]);

  // 미션 초기화. 성공하면 이번 점검 상태를 비워 다음 점검이 깨끗하게 시작되게 한다.
  //
  // 화면 상태도 여기서 함께 비운다. 예전에는 `configuredInspection` 만 지워서,
  // 점검을 끝낸 뒤에도 대시보드의 결함 통계와 로봇 로그에 지난 점검 값이 남아
  // 있었다 — 다음 점검을 시작해야 비로소 지워졌다. 지우는 항목은
  // `submitInspectionSetup` 이 점검 시작 때 비우는 것과 같게 맞춘다.
  // 화면부터 비우고 Agent 초기화는 그 뒤에 건다.
  //
  // 예전에는 `await postMission('reset')` 이 맨 앞이라, Agent 가 응답하지 않으면
  // 그 아래가 통째로 건너뛰어져 **팝업이 닫히지 않았다.** 운영자가 「새로운 점검」을
  // 눌렀는데 화면이 그대로 잡혀 있으면 아무것도 할 수 없다.
  //
  // Agent 초기화 실패는 따로 알린다. 다음 점검은 어차피 새 세션·새 operation 으로
  // 시작하고, 남은 미션은 서버의 버려진 세션 정리가 받아 준다.
  const handleResetMission = useCallback(async () => {
    setConfiguredInspection(null);
    setInspectionResultOpen(false);
    setDefects([]);
    setSession(null);
    setServerLive({});
    setAgentLive({});
    setAgentConnected(false);
    setAiSummary(idleAiSummary());
    setServerEvents([]);
    previousMissionStateRef.current = undefined;
    setCompletionRunningIdentity(null);
    endedByThisBrowserRef.current = false;
    try {
      await postMission('reset');
    } catch (error) {
      setNotice(`Agent 초기화에 실패했습니다: ${error instanceof Error ? error.message : '응답 없음'}`);
    }
    void loadAgentData();
  }, [loadAgentData, postMission, setCompletionRunningIdentity, setConfiguredInspection]);

  const handleNewInspection = useCallback(async () => {
    try {
      await handleResetMission();
      setPage('priority');
    } catch (error) {
      setNotice(`새 점검 준비 실패: ${error instanceof Error ? error.message : '응답 없음'}`);
    }
  }, [handleResetMission]);

  // 「보고서 생성」은 **팝업을 먼저 닫고** 나머지를 뒤에서 돌린다. 생성 요청과 이어지는
  // 요약(LLM)은 합쳐서 수십 초가 걸리는데, 그동안 팝업이 떠 있으면 화면이 잡혀 있다.
  // 세션 번호만 미리 붙잡아 두면 초기화가 먼저 일어나도 서버 요청에는 지장이 없다.
  const handleCreateReportAndReset = useCallback(async () => {
    if (session?.id === undefined) {
      setNotice('보고서를 만들 점검 세션이 없습니다.');
      return;
    }
    const sessionId = String(session.id);
    setInspectionResultOpen(false);
    setNotice('보고서를 만들고 있습니다 — 완료되면 보고서 탭에서 열 수 있습니다.');

    void (async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/sessions/${sessionId}/reports`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
        });
        if (!response.ok) {
          const detail = await response.json().catch(() => null);
          throw new Error(detail?.detail || '보고서 생성에 실패했습니다.');
        }
        const created = asRecord(await response.json().catch(() => ({})));
        const createdId = created.id;

        // 요약(LLM)은 **기다리지 않는다.** 서버가 저장해 두면 보고서 탭에서 열 때
        // 즉시 나온다. 실패해도 그쪽이 다시 생성하므로 결과적으로는 채워진다.
        if (createdId !== undefined) {
          void fetch(`${API_BASE_URL}/reports/${String(createdId)}/document?refresh=true`)
            .catch(() => undefined);
        }
        setNotice('보고서를 만들었습니다 — 보고서 탭에서 열 수 있습니다.');
      } catch (error) {
        // 팝업은 이미 닫혔다. 다시 만들려면 보고서 탭에서 「요약 재생성」을 쓴다.
        setNotice(`보고서 생성 실패: ${error instanceof Error ? error.message : '응답 없음'}`);
      }
    })();

    try {
      // 화면을 옮기지 않는다. 요약이 서버에서 도는 동안 사용자가 원하는 탭으로
      // 자유롭게 이동할 수 있어야 한다.
      await handleResetMission();
    } catch (error) {
      setNotice(`점검 상태 초기화 실패: ${error instanceof Error ? error.message : '응답 없음'}`);
    }
  }, [handleResetMission, session?.id]);

  const openInspectionSetup = useCallback((sectionName: string | null, districtId?: number) => {
    if (!sectionName || districtId === undefined) {
      setNotice('검사를 시작할 동을 선택해 주세요.');
      return;
    }
    // 구간은 첫 번째를 미리 골라 둔다. 동을 정한 순간 구간 목록이 확정되므로
    // 빈 값으로 두면 사용자가 한 번 더 눌러야 하고, 안 누르면 시작이 막힌다.
    const [firstSection = ''] = sectionOptionsFor(sectionName);
    // 로봇·운영자·방향도 미리 골라 둔다. 어차피 쓸 수 있는 로봇은 한 대뿐이고
    // 매번 같은 값을 세 번 고르는 일이 된다. 목록은 그대로 두어 바꿀 수 있다.
    //
    // 번호를 박지 않고 **목록에서 고른다** — 로봇은 지금 투입 가능한(READY) 첫 대를
    // 잡으므로, 나중에 2호기가 들어오고 1호기가 정비에 들어가도 알아서 따라간다.
    const defaultRobot = availableRobots.find(
      (item) => String(item.status ?? '').toUpperCase() === ROBOT_READY_STATUS,
    ) ?? availableRobots[0];
    const [defaultOperator] = availableOperators;
    setInspectionSetup({
      ...createInspectionSetupValues(),
      sectionName,
      section: firstSection,
      districtId,
      robotId: defaultRobot ? String(defaultRobot.id) : '',
      operatorId: defaultOperator ? String(defaultOperator.id) : '',
      direction: 'FORWARD',
    });
    setInspectionSetupOpen(true);
  }, [availableOperators, availableRobots]);

  // 로봇을 움직이는 조작은 확인 코드를 받고 나서 실행한다. 코드가 맞는지는 서버가 본다.
  const [operatorCodePrompt, setOperatorCodePrompt] = useState<{ title: string; run: () => void } | null>(null);
  const requireOperatorCode = useCallback((title: string, run: () => void) => {
    setOperatorCodePrompt({ title, run });
  }, []);

  const updateInspectionSetup = useCallback((field: keyof InspectionSetupValues, value: string) => {
    // 운영자를 바꾸면 코드도 비운다. 앞 사람이 넣어 둔 코드가 남아 있으면
    // 다른 운영자 이름 아래에서 그대로 제출돼 무슨 검사를 통과한 건지 흐려진다.
    setInspectionSetup((current) => field === 'operatorId'
      ? { ...current, operatorId: value, operatorCode: '' }
      : { ...current, [field]: value });
  }, []);

  const submitInspectionSetup = useCallback(async () => {
    const submittedInspection = inspectionSetup;

    // 시작해도 되는지는 **서버가 판단한다** (`POST /sessions` 의 guard_inspection_start).
    // 여기서 미리 걸러 주는 것은 즉시 알려 주기 위한 편의일 뿐이고, 화면 검사만 있으면
    // 개발자 도구로 API 를 직접 불러 지나갈 수 있다. 그래서 이 검사를 통과해도
    // 서버가 다시 본다. 로봇 상태는 폴링으로 받아 둔 값이라 여기서만 본다.
    const robot = availableRobots.find((item) => String(item.id) === submittedInspection.robotId);
    if (robot && String(robot.status ?? '').toUpperCase() !== ROBOT_READY_STATUS) {
      setNotice('로봇과 연결되지 않았습니다.');
      return;
    }

    const operationId = crypto.randomUUID();
    const configuredAt = new Date().toISOString();
    // 확인 코드는 여기서 버린다. `configuredInspection` 은 sessionStorage 에 남는데,
    // 검사에 이미 통과한 값을 저장소까지 끌고 갈 이유가 없다.
    const provisionalInspection = { ...submittedInspection, operatorCode: '', operationId, configuredAt };
    setConfiguredInspection(provisionalInspection);
    setDefects([]);
    setSession(null);
    setServerLive({});
    setAgentLive({});
    setAgentConnected(false);
    setAiSummary(idleAiSummary());
    setServerEvents([]);
    setInspectionResultOpen(false);
    previousMissionStateRef.current = undefined;
    setCompletionRunningIdentity(null);
    endedByThisBrowserRef.current = false;

    let createdSessionId: string | number | undefined;
    let createdSessionRecord: CloudSession | null = null;
    let agentStartAttempted = false;
    let startWasDefinitivelyRejected = false;
    try {
      const response = await fetch(`${API_BASE_URL}/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          district_id: submittedInspection.districtId,
          robot_id: Number(submittedInspection.robotId),
          approved_by: Number(submittedInspection.operatorId),
          // 확인 코드는 서버가 검증한다. 화면은 사용자가 친 값을 그대로 넘길 뿐이다.
          operator_code: submittedInspection.operatorCode,
          mode: 'AUTO',
          // 「점검 시작」을 누른 그 시각. 서버가 자기 시계로 찍으면 요청이 도착한
          // 순간이 되는데, 화면에 보여 준 시각과 다른 값이 기록될 수 있다.
          // 서버는 이 값이 자기 시계와 크게 어긋나면 무시하고 스스로 찍는다.
          started_at: configuredAt,
          survey: {
            survey_location: submittedInspection.sectionName,
            inspection_section: submittedInspection.section,
          },
        }),
      });
      if (!response.ok) {
        // 서버가 왜 막았는지(확인 코드·로봇 상태·점검 중) 그대로 보여 준다.
        const detail = asRecord(await response.json().catch(() => ({}))).detail;
        throw new Error(typeof detail === 'string' && detail ? detail : 'Cloud session 생성 실패');
      }
      const createdSession = asRecord(await response.json());
      createdSessionId = createdSession.id;
      if (createdSessionId === undefined) throw new Error('Cloud session ID 누락');
      createdSessionRecord = createdSession as CloudSession;

      agentStartAttempted = true;
      const started = await handleRobotAction(
        'running',
        submittedInspection.sectionName,
        createdSessionId,
        operationId,
        true,
      );
      if (!started) throw new Error('Hermes 점검 시작 실패');

      setConfiguredInspection({ ...provisionalInspection, sessionId: createdSessionId });
      setSession(createdSession as CloudSession);
      setInspectionSetupOpen(false);
      setPage('dashboard');
      void loadCloudData();
    } catch (error) {
      startWasDefinitivelyRejected = error instanceof RemoteCommandRejectedError;
      if (agentStartAttempted && !startWasDefinitivelyRejected && createdSessionId !== undefined) {
        try {
          await sendRemoteHermesCommand(
            'STOP',
            submittedInspection.sectionName,
            createdSessionId,
            operationId,
          );
        } catch (stopError) {
          setConfiguredInspection({
            ...provisionalInspection,
            sessionId: createdSessionId,
          });
          if (createdSessionRecord) setSession(createdSessionRecord);
          setInspectionSetupOpen(false);
          setPage('dashboard');
          setNotice(
            `점검 시작 응답이 불명확하고 안전 중지에도 실패했습니다: ${stopError instanceof Error ? stopError.message : '응답 없음'}`,
          );
          return;
        }
      }
      if (createdSessionId !== undefined) {
        let abortResponse: Response;
        try {
          abortResponse = await fetch(`${API_BASE_URL}/sessions/${String(createdSessionId)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: 'ABORTED' }),
          });
        } catch (abortError) {
          setConfiguredInspection(null);
          setSession(null);
          setNotice(
            `점검은 시작되지 않았지만 Cloud session 정리 요청에 실패했습니다: ${abortError instanceof Error ? abortError.message : '응답 없음'}`,
          );
          return;
        }
        if (!abortResponse.ok) {
          setConfiguredInspection(null);
          setSession(null);
          setNotice(
            startWasDefinitivelyRejected
              ? `점검 시작은 거부됐지만 Cloud session 정리에 실패했습니다 (${abortResponse.status}).`
              : `Agent는 안전 중지됐지만 Cloud session 정리에 실패했습니다 (${abortResponse.status}).`,
          );
          return;
        }
      }
      setConfiguredInspection(null);
      setNotice(`점검 시작 실패: ${error instanceof Error ? error.message : '응답 없음'}`);
    }
  }, [availableRobots, handleRobotAction, inspectionSetup, loadCloudData, sendRemoteHermesCommand, setCompletionRunningIdentity, setConfiguredInspection, setPage]);

  // 고른 점검 위치·구간을 세션에 심는다. 보고서가 세션의 survey 에서 이 값을 읽어
  // 「점검 위치」·「점검 구간」 칸을 채우므로, 심지 않으면 보고서에 기본값이 나온다.
  //
  // 세션을 만드는 것은 Agent 이고 프론트는 목록을 폴링해서 받는다. 그래서 설정 직후가
  // 아니라 **세션이 나타난 뒤**에 채운다. 세션 하나에 한 번만 보내도록 id 를 기억한다.
  const stampedSessionRef = useRef<string | null>(null);
  useEffect(() => {
    const sessionId = session?.id === undefined ? null : String(session.id);
    if (!configuredInspection || sessionId === null) return;
    if (stampedSessionRef.current === sessionId) return;
    stampedSessionRef.current = sessionId;
    void fetch(`${API_BASE_URL}/sessions/${sessionId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        survey: {
          survey_location: configuredInspection.sectionName,
          inspection_section: configuredInspection.section,
          inspection_direction: configuredInspection.direction,
        },
      }),
    }).catch(() => {
      // 실패하면 보고서에 기본값이 나올 뿐이므로 화면을 막지 않는다. 다음 세션에서
      // 다시 시도할 수 있게 기억을 지운다.
      stampedSessionRef.current = null;
    });
  }, [configuredInspection, session?.id]);

  useEffect(() => { if (!notice) return; const timer = window.setTimeout(() => setNotice(null), 2800); return () => window.clearTimeout(timer); }, [notice]);
  const pageContent = useMemo(() => {
    if (page === 'monitoring') return <MonitoringPage defects={defects} live={live} agentConnected={agentConnected} events={events} session={session} inspection={configuredInspection} robotState={robotState} setPage={setPage} onForceStop={() => requireOperatorCode('긴급 정지', () => { void handleEndInspection().then(() => setPage('dashboard')); })} />;
    if (page === 'report') return <ReportPage session={session} report={report} reports={reports} />;
    if (page === 'threeD') return <ThreeDPage session={session} report={report} reports={reports} />;
    if (page === 'priority') return <PriorityMap active onStartInspection={openInspectionSetup} />;
    return <DashboardPage defects={defects} robotState={robotState} onRobotAction={(next) => requireOperatorCode(next === 'paused' ? '일시 정지' : '다시 시작', () => { void handleInspectionControl(next); })} setPage={setPage} live={live} agentConnected={agentConnected} agentIdentity={agentIdentity} aiSummary={aiSummary} session={session} inspection={configuredInspection} onEndInspection={() => requireOperatorCode('점검 종료', () => { void handleEndInspection(); })} ending={endingInspection} />;
  }, [page, defects, robotState, live, agentConnected, agentIdentity, events, session, report, reports, aiSummary, configuredInspection, handleInspectionControl, openInspectionSetup, handleEndInspection, endingInspection, requireOperatorCode]);
  const selectedRobot = availableRobots.find((item) => String(item.id) === configuredInspection?.robotId) ?? robot;
  const selectedOperator = availableOperators.find((item) => String(item.id) === configuredInspection?.operatorId) ?? operator;
  return <div className="app-shell"><Sidebar page={page} setPage={setPage} open={sidebarOpen} setOpen={setSidebarOpen} live={live} robotName={selectedRobot?.name} /><div className="app-main"><Header page={page} onMenu={() => setSidebarOpen(true)} operator={selectedOperator} /><main className={`page-content page-${page} ${page === 'priority' ? 'priority-page-content' : ''}`}>{pageContent}</main></div>{inspectionSetupOpen && <InspectionSetupModal values={inspectionSetup} robots={availableRobots} operators={availableOperators} onChange={updateInspectionSetup} onClose={() => setInspectionSetupOpen(false)} onSubmit={submitInspectionSetup} />}{inspectionResultOpen && <div className="inspection-setup-backdrop"><section className="inspection-result-modal" role="dialog" aria-modal="true" aria-labelledby="inspection-result-title"><span className="inspection-result-icon"><CircleCheck size={34} /></span><h2 id="inspection-result-title">점검이 종료되었습니다</h2><p className="inspection-result-summary">{configuredInspection?.section ?? configuredInspection?.sectionName ?? '점검 구간 미지정'} · 결함 {defects.length}건</p><div className="inspection-result-actions"><button type="button" className="inspection-cancel" onClick={() => { void handleNewInspection(); }}>새로운 점검</button><button type="button" className="inspection-submit" onClick={() => { void handleCreateReportAndReset(); }}>보고서 생성</button></div></section></div>}{emergencyAlertOpen && <div className="emergency-alert-backdrop"><section className="emergency-alert-modal" role="alertdialog" aria-modal="true" aria-labelledby="emergency-alert-title" aria-describedby="emergency-alert-message"><span className="emergency-alert-icon"><CircleStop size={34} /></span><h2 id="emergency-alert-title">서버 오류 발생</h2><p id="emergency-alert-message">로봇 자세 이상이 감지되어 긴급 정지를 시행했습니다.</p><button type="button" onClick={() => setEmergencyAlertOpen(false)}>확인</button></section></div>}{operatorCodePrompt && <OperatorCodePrompt title={operatorCodePrompt.title} operator={selectedOperator} onCancel={() => setOperatorCodePrompt(null)} onVerified={() => { const { run } = operatorCodePrompt; setOperatorCodePrompt(null); run(); }} />}{backendDown && <div className="toast danger"><ShieldCheck size={18} />백엔드에 연결하지 못했습니다 — cloud_api 와 MongoDB 를 확인하세요</div>}{notice && <div className={`toast notice-toast ${robotState === 'emergency' ? 'danger' : ''}`}>{notice}</div>}</div>;
}
