import { useState, useEffect, useRef } from 'react';
import { Play, Pause, RotateCcw, AlertTriangle, ShieldCheck, Activity, Wifi, Radio, Sliders, Eye, RefreshCw, Info, Ban, HelpCircle, CheckCircle2, Plus, X, Clock, Calendar, Power } from 'lucide-react';
import { SEWER_IMAGES } from '../data';
import { AppTab, InspectionItem, InspectionLog, ReportMeta, STATUS_OPTIONS } from '../types';
import { readCameraHealth } from '../services/camera.mjs';
import { agentWebSocketUrl, driveCommandUiState, eventToInspectionLog, missionUiState, obstaclePerceptionUiState, postMissionAction, progressAnnouncementUiState, readAgentLogs, readDrive, readModels, readMission, readNavigation, readProgressSummary, readYoloStatus, reportProgressDefect, setNavigationVersion, shouldApplyProgressAnnouncement, shouldApplyProgressRestSnapshot, ultrasonicTelemetryUiState, yoloStatusUiState, yoloStreamUrl } from '../services/agent.mjs';
import type { ApiOperator, ApiRobot, ApiSession } from '../types/api';
import { USE_MOCK } from '../api/client';
import { createCaptureDefect } from '../api/defects';
import { createReport } from '../api/reports';
import { createDriveLog, createSession, updateSession } from '../api/sessions';
import { apiDefectToInspectionItem, apiReportToReportMeta } from '../api/adapters';
import { resolveMediaUrl, uploadCapture } from '../api/media';

const YOLO_STREAM_URL = yoloStreamUrl(import.meta.env);
const AGENT_WEBSOCKET_URL = agentWebSocketUrl(import.meta.env);

type DashboardStatus = '점검 중' | '점검 중단' | '복귀 중' | '복귀 완료' | '점검 전' | '준비 중' | '완료' | '막힘 종료' | '긴급 정지' | '오류 종료' | '연결 확인 중';

interface AgentMissionSnapshot {
  missionId?: string | null;
  state: string;
  area?: string | null;
  message?: string;
  agentAction?: string;
  agentReason?: string;
  reasonCode?: string | null;
  distanceM?: number | null;
  reportAvailable?: boolean;
  controlMode?: string;
}

interface DriveCommandSnapshot {
  commandId?: string;
  action: string;
  leftSpeed: number;
  rightSpeed: number;
  safetyApproved: boolean;
  reason: string;
  controlMode?: string;
  perceptionModel?: string;
  decisionModel?: string;
}

interface DriveObservationSnapshot {
  ultrasonicDistanceM?: number | null;
  perceptionConfidence?: number;
  frameAgeS?: number;
  perceptionModel?: string;
  inclinePercent?: number | null;
  pitchDegrees?: number | null;
  pitch?: number | null;
  imuReading?: {
    pitch?: number | null;
  } | null;
  imu_reading?: {
    pitch?: number | null;
  } | null;
}

interface PerceptionSnapshot {
  modelName?: string;
  confidence?: number;
  latencyMs?: number;
  pathBlockedProbability?: number;
  leftFreeScore?: number;
  centerFreeScore?: number;
  rightFreeScore?: number;
  safeForControl?: boolean;
}

interface LocalPlanSnapshot {
  action: string;
  steering: number;
  throttle: number;
  risk: number;
  confidence: number;
  selectedSector: number;
  reason: string;
}

interface DriveStatusSnapshot {
  controlMode: string;
  perceptionModel: string;
  decisionModel: string;
  latestObservation?: DriveObservationSnapshot | null;
  latestPerception?: PerceptionSnapshot | null;
  latestPlan?: LocalPlanSnapshot | null;
  latestCommand?: DriveCommandSnapshot | null;
}

interface ModelRuntimeSnapshot {
  name: string;
  downloaded: boolean;
  loaded: boolean;
  note?: string;
}

interface ActiveModelsSnapshot {
  perception: ModelRuntimeSnapshot;
  decision: ModelRuntimeSnapshot;
}

interface NavigationStatusSnapshot {
  activeVersion: 'v1' | 'v2';
  v1Available: boolean;
  v2Available: boolean;
  v1Label: string;
  v2Label: string;
}

interface ProgressAnnouncementSnapshot {
  revision?: number;
  missionId?: string | null;
  missionState?: string;
  eventType?: 'DEFECT_DETECTED' | 'OBSTACLE_DETECTED' | 'OBSTACLE_AVOIDANCE_COMPLETED' | 'EXPLORATION_UNAVAILABLE' | null;
  message?: string;
  sourceModel?: string;
  generatedAt?: string | null;
  latencyMs?: number | null;
  status: 'idle' | 'pending' | 'ready' | 'unavailable' | 'error';
}

interface YoloStatusSnapshot {
  model?: string | null;
  modelName?: string | null;
  detections?: number | null;
  detectionCount?: number | null;
  latencyMs?: number | null;
  latency_ms?: number | null;
  error?: string | null;
}

interface RealTimeMonitorProps {
  onViewDefect: (item: InspectionItem) => void;
  onNavigateToReport: () => void;
  newInspectionTrigger: number;
  onAddReport: (newReport: ReportMeta, newInsps: InspectionItem[]) => void;
  reportsCount: number;
  inspectionsCount: number;
  inspections: InspectionItem[];
  activeTab?: AppTab;
  onUpdateInspection: (id: string, changes: Partial<InspectionItem>) => void;
  operators: ApiOperator[];
  robots: ApiRobot[];
}

// Extensive list of 9 mock defect items to support real center-table pagination (1, 2, 3)
const PAGINATED_DEFECTS: InspectionItem[] = [
  // Page 1
  {
    id: 'INSP-001',
    defectType: '이음부 결합',
    severity: 'critical',
    date: '2026-07-20',
    time: '13:38:34',
    location: 'MH-22905 ~ MH-22439',
    imageUrl: SEWER_IMAGES.image5,
    reportId: 'REP-20260720-001',
    confidence: 94.8,
    aiModel: 'SewerNet-V4.2',
    status: '검토 필요',
    coordinates: '37.4624, 126.8268',
    camId: 'SW-D04-012',
    originalImageId: '#IMG_24905',
    recommendation: '이음부 간격이 허용 범위를 초과하여 누수 및 토사 유입의 위험이 높습니다. 3개월 이내 부분 비굴착 보수 공사(CIPP 패커 공법)를 권장합니다.',
    timeline: []
  },
  {
    id: 'INSP-002',
    defectType: '퇴적물',
    severity: 'warning',
    date: '2026-07-20',
    time: '13:32:11',
    location: 'MH-22439 ~ MH-22102',
    imageUrl: SEWER_IMAGES.image7,
    reportId: 'REP-20260720-001',
    confidence: 91.2,
    aiModel: 'SewerNet-V4.2',
    status: '미처리',
    coordinates: '37.4619, 126.8255',
    camId: 'SW-D04-010',
    originalImageId: '#IMG_24810',
    recommendation: '퇴적물이 관로 저부의 30% 이상을 차지하고 있어 통수 능력을 저하시킵니다. 고압 준설차 청소가 권고됩니다.',
    timeline: []
  },
  {
    id: 'INSP-003',
    defectType: '균열',
    severity: 'warning',
    date: '2026-07-20',
    time: '13:24:08',
    location: 'MH-22102 ~ MH-21840',
    imageUrl: SEWER_IMAGES.image8,
    reportId: 'REP-20260720-001',
    confidence: 93.5,
    aiModel: 'SewerNet-V4.2',
    status: '담당자 배정',
    coordinates: '37.4611, 126.8241',
    camId: 'SW-D04-008',
    originalImageId: '#IMG_24750',
    recommendation: '종방향 표면 미세 균열이 발생했습니다. 즉각 붕괴 위험은 없으나 주기적 모니터링이 필요합니다.',
    timeline: []
  },
  // Page 2
  {
    id: 'INSP-004',
    defectType: '표면 균열',
    severity: 'warning',
    date: '2026-07-20',
    time: '14:15:10',
    location: 'MH-22439 ~ MH-22102',
    imageUrl: SEWER_IMAGES.image4,
    reportId: 'REP-20260720-001',
    confidence: 89.2,
    aiModel: 'SewerNet-V4.2',
    status: '담당자 배정',
    coordinates: '37.4615, 126.8248',
    camId: 'SW-D04-011',
    originalImageId: '#IMG_24888',
    recommendation: '구조적 균열이 미세하게 감지되었습니다. 주기적인 균열 상태 관찰을 추천합니다.',
    timeline: []
  },
  {
    id: 'INSP-005',
    defectType: '수목 뿌리 침투',
    severity: 'critical',
    date: '2026-07-20',
    time: '15:10:18',
    location: 'MH-21510 ~ MH-21200',
    imageUrl: SEWER_IMAGES.image9,
    reportId: 'REP-20260720-001',
    confidence: 98.4,
    aiModel: 'SewerNet-V4.2',
    status: '미처리',
    coordinates: '37.4590, 126.8210',
    camId: 'SW-D04-015',
    originalImageId: '#IMG_25100',
    recommendation: '관로 내부로 가로수 뿌리가 자라 침입했습니다. 커터를 투입해 뿌리를 제거한 뒤 부분 보수가 시급합니다.',
    timeline: []
  },
  {
    id: 'INSP-006',
    defectType: '관 파손',
    severity: 'critical',
    date: '2026-07-20',
    time: '15:45:22',
    location: 'MH-21200 ~ MH-20900',
    imageUrl: SEWER_IMAGES.image5,
    reportId: 'REP-20260720-001',
    confidence: 95.1,
    aiModel: 'SewerNet-V4.2',
    status: '검토 필요',
    coordinates: '37.4582, 126.8202',
    camId: 'SW-D04-017',
    originalImageId: '#IMG_25205',
    recommendation: '관 외벽 일부 파손으로 인해 주변 토사가 유입될 위험이 감지되었습니다. 부분 비굴착 수밀 보강 공사가 제안됩니다.',
    timeline: []
  },
  // Page 3
  {
    id: 'INSP-007',
    defectType: '토사 퇴적',
    severity: 'warning',
    date: '2026-07-20',
    time: '16:02:11',
    location: 'MH-20900 ~ MH-20600',
    imageUrl: SEWER_IMAGES.image7,
    reportId: 'REP-20260720-001',
    confidence: 92.0,
    aiModel: 'SewerNet-V4.2',
    status: '미처리',
    coordinates: '37.4571, 126.8191',
    camId: 'SW-D04-019',
    originalImageId: '#IMG_25301',
    recommendation: '관로 바닥에 흙과 사토가 가라앉아 흐름을 지연시키고 있습니다. 기계식 준설 청소를 지시해 주십시오.',
    timeline: []
  },
  {
    id: 'INSP-008',
    defectType: '유수 유출',
    severity: 'warning',
    date: '2026-07-20',
    time: '16:22:45',
    location: 'MH-20600 ~ MH-20300',
    imageUrl: SEWER_IMAGES.image8,
    reportId: 'REP-20260720-001',
    confidence: 90.7,
    aiModel: 'SewerNet-V4.2',
    status: '담당자 배정',
    coordinates: '37.4560, 126.8180',
    camId: 'SW-D04-022',
    originalImageId: '#IMG_25410',
    recommendation: '관로 이음부 틈새로 맑은 지하수가 내부로 유입되는 양상입니다. 이음 수밀 수리를 시행해 주십시오.',
    timeline: []
  },
  {
    id: 'INSP-009',
    defectType: '이음부 이탈',
    severity: 'critical',
    date: '2026-07-20',
    time: '16:50:34',
    location: 'MH-20300 ~ MH-20000',
    imageUrl: SEWER_IMAGES.image5,
    reportId: 'REP-20260720-001',
    confidence: 96.2,
    aiModel: 'SewerNet-V4.2',
    status: '검토 필요',
    coordinates: '37.4549, 126.8169',
    camId: 'SW-D04-025',
    originalImageId: '#IMG_25502',
    recommendation: '접합 지점의 관이 축 방향으로 심하게 미끄러져 벌어졌습니다. 누수 방지를 위한 패커 공법 긴급 보수가 추천됩니다.',
    timeline: []
  }
];

export default function RealTimeMonitor({
  onViewDefect,
  onNavigateToReport,
  newInspectionTrigger,
  onAddReport,
  reportsCount,
  inspectionsCount,
  inspections,
  activeTab,
  onUpdateInspection,
  operators,
  robots,
}: RealTimeMonitorProps) {

  // Robot Status State: '점검 중' | '점검 중단' | '복귀 중' | '복귀 완료' | '점검 전'
  const [status, setStatus] = useState<DashboardStatus>('점검 전');
  const [agentConnected, setAgentConnected] = useState<boolean>(false);
  const [agentMission, setAgentMission] = useState<AgentMissionSnapshot>({
    state: 'IDLE',
    message: 'AI Agent 서버 연결을 확인하고 있습니다.',
    distanceM: null,
    reportAvailable: false,
    controlMode: 'simulation-no-motor',
  });
  const [driveStatus, setDriveStatus] = useState<DriveStatusSnapshot>({
    controlMode: 'simulation-no-motor',
    perceptionModel: 'opencv-pipe',
    decisionModel: 'rule-policy',
    latestCommand: null,
  });
  const [ultrasonicObservation, setUltrasonicObservation] = useState<DriveObservationSnapshot | null>(null);
  const [obstaclePerception, setObstaclePerception] = useState<PerceptionSnapshot | null>(null);
  const [localPlan, setLocalPlan] = useState<LocalPlanSnapshot | null>(null);
  const [telemetryTime, setTelemetryTime] = useState<string>('--:--:--');
  const [modelStatus, setModelStatus] = useState<ActiveModelsSnapshot>({
    perception: { name: 'segformer-b2', downloaded: false, loaded: false },
    decision: { name: 'qwen2.5-vl-3b-instruct-awq', downloaded: false, loaded: false },
  });
  const [navigationStatus, setNavigationStatus] = useState<NavigationStatusSnapshot>({
    activeVersion: 'v1',
    v1Available: true,
    v2Available: false,
    v1Label: 'V1 LocalFreeSpacePlanner',
    v2Label: 'V2 Neural Navigation Policy',
  });
  const [navigationChanging, setNavigationChanging] = useState<boolean>(false);

  // Frozen distance snapshot when manual defect adding is clicked
  const [manualDefectDistance, setManualDefectDistance] = useState<number>(0);

  // Center Defect Table Page (1, 2, 3)
  const [defectPage, setDefectPage] = useState<number>(1);

  // State to control visibility of the final inspection result popup
  const [showResultPopup, setShowResultPopup] = useState<boolean>(false);

  // Parameter states for Inspection Setup
  const [inspectionSection, setInspectionSection] = useState<string>('광주광역시 하남산단 6번로');
  const [pipeType, setPipeType] = useState<string>('PVC (400 mm)');
  const [inspectionDirection, setInspectionDirection] = useState<string>('정방향');
  const [startLocation, setStartLocation] = useState<string>('MH-22905');
  const [inspectionStartTime, setInspectionStartTime] = useState<string>('2026-07-20 13:30:15');
  const [selectedRobotId, setSelectedRobotId] = useState<number | null>(null);
  const [selectedOperatorId, setSelectedOperatorId] = useState<number | null>(null);
  const [cloudSession, setCloudSession] = useState<ApiSession | null>(null);

  // Popup visibility states
  const [showSetupPopup, setShowSetupPopup] = useState<boolean>(false);
  const [showManualAddPopup, setShowManualAddPopup] = useState<boolean>(false);

  // Manual Defect Form States
  const [manualDefectType, setManualDefectType] = useState<string>('균열');
  const [manualSeverity, setManualSeverity] = useState<string>('warning');
  const [manualCaptureBlob, setManualCaptureBlob] = useState<Blob | null>(null);
  const [manualCaptureUrl, setManualCaptureUrl] = useState<string>('');
  const [isCapturing, setIsCapturing] = useState<boolean>(false);
  const imageRef = useRef<HTMLImageElement>(null);
  const [cameraConnected, setCameraConnected] = useState<boolean>(false);
  const [cameraName, setCameraName] = useState<string>('연결 확인 중');
  const [yoloStreamConnected, setYoloStreamConnected] = useState<boolean>(false);
  const [yoloStatus, setYoloStatus] = useState<YoloStatusSnapshot | null>(null);
  const [streamVersion, setStreamVersion] = useState<number>(Date.now());
  const cameraStateRef = useRef<boolean | null>(null);

  // Defect detection is outside the current autonomy milestone; never preload sample defects.
  const [defects, setDefects] = useState<InspectionItem[]>([]);

  useEffect(() => {
    if (selectedRobotId === null && robots.length > 0) {
      setSelectedRobotId(robots[0].id);
    }
    if (selectedOperatorId === null && operators.length > 0) {
      setSelectedOperatorId(operators[0].id);
    }
  }, [operators, robots, selectedOperatorId, selectedRobotId]);

  // Synchronize changes made in DetailModal popup back to defects
  useEffect(() => {
    setDefects((prevDefects) => {
      return prevDefects.map((def) => {
        const found = inspections.find((ins) => ins.id === def.id);
        if (found) {
          return { ...def, status: found.status, actionType: found.actionType };
        }
        return def;
      });
    });
  }, [inspections]);

  // Simulation states
  const [distance, setDistance] = useState<number>(0.00);
  const [speed, setSpeed] = useState<number>(0.00);
  const [latency, setLatency] = useState<number | null>(null);
  const [timeStr, setTimeStr] = useState<string>('14:55:04');
  const [dateStr, setDateStr] = useState<string>(() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  });

  // Dynamic Live Driving Logs
  const [logs, setLogs] = useState<InspectionLog[]>([
    { time: new Date().toTimeString().split(' ')[0], text: '카메라와 Raspberry Pi 연결 상태를 확인하고 있습니다.', type: 'info' },
    { time: new Date().toTimeString().split(' ')[0], text: 'AI Agent와 모터 제어는 아직 연결되지 않았습니다.', type: 'warn' },
  ]);
  const [progressSummary, setProgressSummary] = useState<string>('안내 이벤트 대기 중');
  const [progressSummaryTime, setProgressSummaryTime] = useState<string>('--:--:--');
  const [progressSummaryTone, setProgressSummaryTone] = useState<'idle' | 'pending' | 'ready' | 'error'>('idle');
  const progressRevisionRef = useRef<number>(-1);
  const [completedInspectionLogs, setCompletedInspectionLogs] = useState<InspectionLog[]>([]);
  const resultUi = missionUiState(agentMission);
  const driveUi = driveCommandUiState(driveStatus.latestCommand);
  const ultrasonicUi = ultrasonicTelemetryUiState(ultrasonicObservation);
  const obstacleUi = obstaclePerceptionUiState(obstaclePerception);
  const yoloUi = yoloStatusUiState(yoloStatus);
  const missionActive = ['PREPARING', 'RUNNING', 'STOPPING'].includes(agentMission.state);
  const missionTerminal = ['COMPLETED', 'FAILED_BLOCKED', 'EMERGENCY_STOP', 'ERROR'].includes(agentMission.state);
  const navigationSelectable = agentMission.state === 'IDLE';

  // Helper to add logs in real-time
  const addLog = (text: string, type: 'info' | 'warn' | 'success' | 'alert' = 'info') => {
    const now = new Date();
    const curTime = now.toTimeString().split(' ')[0];
    setLogs((prev) => [
      { time: curTime, text, type },
      ...prev,
    ].slice(0, 200));
  };

  const handleNavigationVersionChange = async (version: 'v1' | 'v2') => {
    setNavigationChanging(true);
    try {
      const navigation = await setNavigationVersion(fetch, version, import.meta.env) as NavigationStatusSnapshot;
      setNavigationStatus(navigation);
      setLocalPlan(null);
      setDriveStatus((previous) => ({
        ...previous,
        decisionModel: version === 'v2'
          ? 'pipecheckkk-ai-v2-neural-policy'
          : 'depth-local-planner-v1',
        latestPlan: null,
        latestCommand: null,
      }));
      addLog(`주행 버전을 ${version.toUpperCase()}로 변경했습니다.`, 'success');
    } catch (error) {
      addLog(`주행 버전 변경 실패: ${error instanceof Error ? error.message : '응답 없음'}`, 'alert');
    } finally {
      setNavigationChanging(false);
    }
  };

  const applyProgressAnnouncement = (announcement: ProgressAnnouncementSnapshot) => {
    if (!shouldApplyProgressAnnouncement(progressRevisionRef.current, announcement)) return;
    if (Number.isInteger(announcement.revision)) {
      progressRevisionRef.current = Number(announcement.revision);
    }
    const ui = progressAnnouncementUiState(announcement) as {
      message: string;
      tone: 'idle' | 'pending' | 'ready' | 'error';
    };
    setProgressSummary(ui.message);
    setProgressSummaryTone(ui.tone);
    setProgressSummaryTime(
      typeof announcement.generatedAt === 'string'
        ? announcement.generatedAt.slice(11, 19)
        : '--:--:--',
    );
    if (announcement.status === 'ready') {
      setModelStatus((previous) => ({
        ...previous,
        decision: { ...previous.decision, loaded: true },
      }));
    }
  };

  const applyMissionSnapshot = (mission: AgentMissionSnapshot) => {
    const ui = missionUiState(mission);
    setAgentMission(mission);
    if (mission.area) setInspectionSection(mission.area);
    setStatus(ui.status as DashboardStatus);
    setShowResultPopup(ui.showResult);
    setDistance(Number.isFinite(mission.distanceM) ? Number(mission.distanceM) : 0);
  };

  useEffect(() => {
    let disposed = false;
    let progressReceivedFromSocket = false;
    const socket = new WebSocket(AGENT_WEBSOCKET_URL);

    readMission(fetch, import.meta.env)
      .then((mission) => {
        if (!disposed) applyMissionSnapshot(mission as AgentMissionSnapshot);
      })
      .catch((error) => {
        if (!disposed) addLog(`AI Agent 상태 조회 실패: ${error instanceof Error ? error.message : '응답 없음'}`, 'alert');
      });

    readProgressSummary(fetch, import.meta.env)
      .then((announcement) => {
        if (!disposed && shouldApplyProgressRestSnapshot(progressReceivedFromSocket)) {
          applyProgressAnnouncement(announcement as ProgressAnnouncementSnapshot);
        }
      })
      .catch((error) => {
        if (!disposed && shouldApplyProgressRestSnapshot(progressReceivedFromSocket)) {
          setProgressSummary('Qwen 안내 멘트 상태를 불러오지 못했습니다.');
          setProgressSummaryTone('error');
          addLog(`Qwen 진행 요약 조회 실패: ${error instanceof Error ? error.message : '응답 없음'}`, 'warn');
        }
      });

    readAgentLogs(fetch, import.meta.env)
      .then((events) => {
        if (!disposed) {
          setLogs(events.slice().reverse().map(eventToInspectionLog) as InspectionLog[]);
        }
      })
      .catch((error) => {
        if (!disposed) addLog(`AI Agent 로그 조회 실패: ${error instanceof Error ? error.message : '응답 없음'}`, 'warn');
      });

    readDrive(fetch, import.meta.env)
      .then((drive) => {
        if (!disposed) {
          const snapshot = drive as DriveStatusSnapshot;
          setDriveStatus(snapshot);
          setUltrasonicObservation(snapshot.latestObservation || null);
          setObstaclePerception(snapshot.latestPerception || null);
          setLocalPlan(snapshot.latestPlan || null);
        }
      })
      .catch((error) => {
        if (!disposed) addLog(`주행 상태 조회 실패: ${error instanceof Error ? error.message : '응답 없음'}`, 'warn');
      });

    readModels(fetch, import.meta.env)
      .then((models) => {
        if (!disposed) setModelStatus(models as ActiveModelsSnapshot);
      })
      .catch((error) => {
        if (!disposed) addLog(`AI 모델 상태 조회 실패: ${error instanceof Error ? error.message : '응답 없음'}`, 'warn');
      });

    readNavigation(fetch, import.meta.env)
      .then((navigation) => {
        if (!disposed) setNavigationStatus(navigation as NavigationStatusSnapshot);
      })
      .catch((error) => {
        if (!disposed) addLog(`주행 버전 조회 실패: ${error instanceof Error ? error.message : '응답 없음'}`, 'warn');
      });

    socket.onopen = () => {
      if (disposed) return;
      setAgentConnected(true);
      addLog('PipeCheckkk 통합 AI Agent 이벤트 스트림에 연결됐습니다.', 'success');
    };
    socket.onmessage = (message) => {
      if (disposed) return;
      try {
        const payload = JSON.parse(message.data);
        if (payload.type === 'mission.snapshot') {
          applyMissionSnapshot(payload.mission as AgentMissionSnapshot);
        } else if (payload.type === 'progress.summary') {
          progressReceivedFromSocket = true;
          applyProgressAnnouncement(payload.summary as ProgressAnnouncementSnapshot);
        } else if (payload.type === 'agent.event') {
          const log = eventToInspectionLog(payload.event) as InspectionLog;
          setLogs((previous) => [log, ...previous].slice(0, 200));
        } else if (payload.type === 'perception.result') {
          setObstaclePerception(payload.perception as PerceptionSnapshot);
          setUltrasonicObservation(payload.observation as DriveObservationSnapshot);
          setTelemetryTime(new Date().toTimeString().split(' ')[0]);
        } else if (payload.type === 'planner.decision') {
          const plan = payload.plan as LocalPlanSnapshot;
          setLocalPlan(plan);
          addLog(
            `[PLANNER] ${plan.reason}`,
            plan.action === 'STOP' ? 'alert' : plan.risk >= 0.70 ? 'warn' : 'info',
          );
        } else if (payload.type === 'drive.command') {
          const command = payload.command as DriveCommandSnapshot;
          setDriveStatus((previous) => ({
            ...previous,
            controlMode: command.controlMode || previous.controlMode,
            perceptionModel: command.perceptionModel || previous.perceptionModel,
            decisionModel: command.decisionModel || previous.decisionModel,
            latestCommand: command,
          }));
          const commandUi = driveCommandUiState(command);
          addLog(`Simulation 주행 명령: ${commandUi.steeringLabel} · ${commandUi.wheelLabel}`, command.safetyApproved ? 'info' : 'alert');
        } else if (payload.type === 'navigation.version') {
          setNavigationStatus(payload.navigation as NavigationStatusSnapshot);
        }
      } catch {
        addLog('AI Agent 이벤트 형식이 올바르지 않아 무시했습니다.', 'warn');
      }
    };
    socket.onerror = () => {
      if (!disposed) setAgentConnected(false);
    };
    socket.onclose = () => {
      if (disposed) return;
      setAgentConnected(false);
      addLog('AI Agent 이벤트 연결이 끊겼습니다. 주행 명령은 허용되지 않습니다.', 'alert');
    };

    return () => {
      disposed = true;
      socket.close();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    const checkCamera = async () => {
      const startedAt = performance.now();
      const health = await readCameraHealth(fetch, import.meta.env);
      if (cancelled) return;

      setLatency(Math.round(performance.now() - startedAt));
      setCameraConnected(health.connected);
      setCameraName(health.camera || '카메라 오프라인');

      if (cameraStateRef.current !== health.connected) {
        cameraStateRef.current = health.connected;
        addLog(
          health.connected
            ? `${health.camera || 'Raspberry Pi 카메라'} 영상 스트림 연결 완료.`
            : `카메라 연결 실패: ${health.error || '응답 없음'}`,
          health.connected ? 'success' : 'alert',
        );
      }
    };

    checkCamera();
    const timer = window.setInterval(checkCamera, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    const checkYoloStatus = async () => {
      try {
        const snapshot = await readYoloStatus(fetch, import.meta.env) as YoloStatusSnapshot;
        if (!cancelled) {
          setYoloStatus(snapshot);
          setTelemetryTime(new Date().toTimeString().split(' ')[0]);
        }
      } catch (error) {
        if (!cancelled) {
          setYoloStatus({ error: error instanceof Error ? error.message : 'YOLO 상태 응답 없음' });
        }
      }
    };

    checkYoloStatus();
    const timer = window.setInterval(checkYoloStatus, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  // References for interval
  const intervalRef = useRef<any>(null);

  const captureCurrentFrame = async (): Promise<Blob> => {
    const image = imageRef.current;
    if (!image || !image.complete || image.naturalWidth === 0) throw new Error('캡처할 영상 화면이 아직 준비되지 않았습니다.');

    const canvas = document.createElement('canvas');
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('화면 캡처 기능을 사용할 수 없습니다.');
    context.drawImage(image, 0, 0, canvas.width, canvas.height);

    const blob = await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob((result) => {
        if (result) resolve(result);
        else reject(new Error('이미지 변환에 실패했습니다.'));
      }, 'image/png');
    });

    return blob;
  };

  const handleOpenManualDefect = async () => {
    if (status !== '점검 중' && status !== '점검 중단') return;
    setManualDefectDistance(distance);
    setIsCapturing(true);
    try {
      const capture = await captureCurrentFrame();
      setManualCaptureBlob(capture);
      setManualCaptureUrl(URL.createObjectURL(capture));
      setShowManualAddPopup(true);
    } catch (error) {
      alert(error instanceof Error ? error.message : '화면 캡처에 실패했습니다.');
    } finally {
      setIsCapturing(false);
    }
  };

  // Synchronize time display
  useEffect(() => {
    const tInterval = setInterval(() => {
      const now = new Date();
      setTimeStr(now.toTimeString().split(' ')[0]);
      setDateStr(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`);
    }, 1000);
    return () => clearInterval(tInterval);
  }, []);

  // Motor telemetry is not connected yet. Never fabricate distance, speed, or movement.
  useEffect(() => {
    setSpeed(0);
    if (intervalRef.current) clearInterval(intervalRef.current);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [status]);

  // Pause driving when navigating away to other tabs like 'report'
  useEffect(() => {
    if (activeTab && activeTab !== 'realtime' && status === '점검 중') {
      setStatus('점검 중단');
      addLog('사용자가 대시보드/보고서 화면으로 이동하여 로봇 주행이 일시중지되었습니다.', 'warn');
      setSpeed(0);
    }
  }, [activeTab]);

  // Handle external trigger for new inspection (from "+ 새로운 점검" button click)
  useEffect(() => {
    if (newInspectionTrigger > 0) {
      setStatus('점검 전');
      setDistance(0.00);
      setSpeed(0.00);
      setDefects([]);
      const now = new Date();
      const curTime = now.toTimeString().split(' ')[0];
      setLogs([
        { time: curTime, text: '신규 카메라 관제 세션이 준비되었습니다.', type: 'info' },
        { time: curTime, text: '모터·초음파 telemetry는 아직 연결되지 않았습니다.', type: 'warn' }
      ]);
    }
  }, [newInspectionTrigger]);

  // Motor actions remain disabled until the Raspberry Pi safety API is implemented.
  const handleToggleDrive = () => {
    setStatus('점검 중단');
    setSpeed(0);
    addLog('주행 명령 차단: 모터 안전 제어 API가 아직 연결되지 않았습니다.', 'warn');
  };

  const handleReturn = () => {
    setStatus('점검 중단');
    setSpeed(0);
    addLog(`복귀 명령 차단: ${startLocation} 방향 복귀 제어는 아직 활성화되지 않았습니다.`, 'warn');
  };

  // Close/End inspection ('점검 종료' -> '점검 전')
  const handleEndInspection = async () => {
    try {
      const snapshot = await postMissionAction(fetch, 'complete', {}, import.meta.env);
      setCompletedInspectionLogs([...logs]);
      applyMissionSnapshot(snapshot as AgentMissionSnapshot);
      if (!USE_MOCK && cloudSession) {
        const endedAt = new Date().toISOString();
        const logResults = await Promise.allSettled(
          logs.slice(0, 50).map((log) =>
            createDriveLog(cloudSession.id, {
              ultrasonic_distance: ultrasonicObservation?.ultrasonicDistanceM ?? null,
              imu_reading: ultrasonicObservation?.imuReading ?? ultrasonicObservation?.imu_reading ?? null,
              command: driveStatus.latestCommand?.action ?? null,
              command_source: 'AI_AGENT',
              agent_state: String((snapshot as AgentMissionSnapshot).state || 'COMPLETED'),
              note: log.text,
              logged_at: endedAt,
            }),
          ),
        );
        const failedLogs = logResults.filter((result) => result.status === 'rejected').length;
        await updateSession(cloudSession.id, {
          status: 'COMPLETED',
          ended_at: endedAt,
        });
        setCloudSession((previous) => previous ? {
          ...previous,
          status: 'COMPLETED',
          ended_at: endedAt,
        } : previous);
        addLog(
          failedLogs > 0
            ? `Docker DB 세션 종료 완료 · 로그 ${failedLogs}건 저장 실패`
            : `Docker DB 세션 ${cloudSession.id}과 주행 로그 저장 완료`,
          failedLogs > 0 ? 'warn' : 'success',
        );
      }
    } catch (error) {
      addLog(`임무 종료 요청 실패: ${error instanceof Error ? error.message : '응답 없음'}`, 'alert');
    }
  };

  const handleStartInspection = async () => {
    const now = new Date();
    const autoTime = `${now.toISOString().slice(0, 10)} ${now.toTimeString().split(' ')[0]}`;
    let createdSession: ApiSession | null = null;
    try {
      if (!USE_MOCK) {
        if (selectedRobotId === null) {
          throw new Error('Docker DB에 등록된 로봇을 선택해 주세요.');
        }
        createdSession = await createSession({
          district_id: Number(import.meta.env.VITE_DEFAULT_DISTRICT_ID ?? 1),
          priority_score_id: Number(import.meta.env.VITE_DEFAULT_PRIORITY_SCORE_ID ?? 1),
          robot_id: selectedRobotId,
          approved_by: selectedOperatorId,
          mode: 'AUTO',
        });
        setCloudSession(createdSession);
      }
      const snapshot = await postMissionAction(
        fetch,
        'start',
        { area: inspectionSection },
        import.meta.env,
      );
      setInspectionStartTime(autoTime);
      setDistance(0);
      setSpeed(0);
      setDefects([]);
      setCompletedInspectionLogs([]);
      setShowSetupPopup(false);
      setLogs([
        {
          time: now.toTimeString().split(' ')[0],
          text: `[MISSION] ${inspectionSection} Agent 관제 임무를 시작했습니다. 모터는 아직 미연동입니다.`,
          type: 'success',
        },
        {
          time: now.toTimeString().split(' ')[0],
          text: '[SCOPE] 결함 탐지 제외 · 자율주행 로직은 simulation-no-motor 모드입니다.',
          type: 'warn',
        },
      ]);
      applyMissionSnapshot(snapshot as AgentMissionSnapshot);
    } catch (error) {
      if (createdSession) {
        void updateSession(createdSession.id, {
          status: 'ABORTED',
          ended_at: new Date().toISOString(),
        }).catch((updateError) => {
          console.error('[FastAPI] 실패한 세션 정리 오류', updateError);
        });
      }
      addLog(`AI Agent 임무 시작 실패: ${error instanceof Error ? error.message : '응답 없음'}`, 'alert');
    }
  };

  const handleResetMission = async () => {
    try {
      const snapshot = await postMissionAction(fetch, 'reset', {}, import.meta.env);
      setDefects([]);
      setCompletedInspectionLogs([]);
      setCloudSession(null);
      setShowResultPopup(false);
      setLogs([
        {
          time: new Date().toTimeString().split(' ')[0],
          text: '[MISSION] 신규 점검을 위해 Agent를 IDLE 상태로 초기화했습니다.',
          type: 'info',
        },
      ]);
      applyMissionSnapshot(snapshot as AgentMissionSnapshot);
    } catch (error) {
      addLog(`Agent 초기화 실패: ${error instanceof Error ? error.message : '응답 없음'}`, 'alert');
    }
  };

  const handleSystemShutdown = () => {
    if (confirm('실시간 점검 시스템을 종료하고 메인 대시보드로 복귀하시겠습니까?')) {
      setStatus('점검 전');
      setDistance(0);
      setSpeed(0);
      onNavigateToReport();
    }
  };

  // Generate Report using dynamic defects
  const handleCreateReport = async () => {
    if (!USE_MOCK) {
      if (!cloudSession) throw new Error('보고서를 생성할 Docker DB 세션이 없습니다.');
      const apiReport = await createReport(cloudSession.id);
      const newReport = apiReportToReportMeta(
        apiReport,
        cloudSession,
        [],
        [],
      );
      const updatedInsps = defects.map((defect) => ({
        ...defect,
        reportId: String(apiReport.id),
      }));
      onAddReport(newReport, updatedInsps);
      addLog(`Docker DB 보고서 ${apiReport.id} 생성 완료`, 'success');
      return;
    }

    const nextRepId = `REP-${new Date().toISOString().slice(0, 10).replace(/-/g, '')}-${String(reportsCount + 1).padStart(3, '0')}`;

    const newReport: ReportMeta = {
      id: nextRepId,
      region: inspectionSection,
      date: new Date().toISOString().slice(0, 10),
      duration: '01:45:20',
      totalInspected: Math.floor(distance) || 120,
      criticalCount: defects.filter(d => d.severity === 'critical').length,
      healthScore: Math.max(60, +(100 - defects.length * 8.5).toFixed(1)),
      gradeZone: defects.length === 0 ? 'A등급' : defects.length <= 2 ? 'B등급' : 'C등급',
      inspectionLogs: completedInspectionLogs,
    };

    // Update the report ID of all live defects
    const updatedInsps = defects.map((d, index) => ({
      ...d,
      id: `INSP-${String(inspectionsCount + index + 1).padStart(3, '0')}`,
      reportId: nextRepId,
      location: d.location.includes('(주행 중)') ? `${startLocation} ~ (주행 ${distance.toFixed(0)}m 지점)` : d.location
    }));

    onAddReport(newReport, updatedInsps);
  };

  const handleCreateReportAndReset = async () => {
    try {
      await handleCreateReport();
      await handleResetMission();
      onNavigateToReport();
    } catch (error) {
      addLog(`보고서 생성 실패: ${error instanceof Error ? error.message : '응답 없음'}`, 'alert');
    }
  };

  const getApiDefectType = (label: string) => {
    if (label.includes('균열')) return 'CRACK';
    if (label.includes('퇴적')) return 'DEBRIS';
    if (label.includes('뿌리')) return 'ROOT';
    if (label.includes('막힘') || label.includes('이물질')) return 'BLOCKAGE';
    if (label.includes('표면') || label.includes('파손')) return 'SURFACE_DAMAGE';
    return 'OTHER';
  };

  const handleAddManualDefect = async () => {
    const now = new Date();
    const nowTime = now.toTimeString().split(' ')[0];
    try {
      if (!manualCaptureBlob) throw new Error('저장할 캡처 이미지가 없습니다.');

      let newId = `INSP-M${String(defects.length + 1).padStart(3, '0')}`;
      let reportId = 'REP-LIVE-PENDING';
      let savedItem: InspectionItem | null = null;
      let snapshotPath = manualCaptureUrl;
      let displayImageUrl = manualCaptureUrl;

      if (!USE_MOCK) {
        if (!cloudSession) throw new Error('진행 중인 Docker DB 세션이 없습니다.');
        const uploadedCapture = await uploadCapture(manualCaptureBlob);
        snapshotPath = uploadedCapture.snapshot_path;
        displayImageUrl = resolveMediaUrl(snapshotPath);
        const savedDefect = await createCaptureDefect(cloudSession.id, {
          defect_type: getApiDefectType(manualDefectType),
          severity: manualSeverity.toUpperCase(),
          confidence: 1,
          approx_position_m: manualDefectDistance,
          snapshot_path: snapshotPath,
          detected_at: now.toISOString(),
        });
        savedItem = apiDefectToInspectionItem(savedDefect, reportId);
        newId = String(savedDefect.id);
      }

      const manualItem: InspectionItem = {
        ...(savedItem ?? {}),
        id: newId,
        defectType: manualDefectType,
        severity: manualSeverity as 'critical' | 'warning',
        date: now.toISOString().slice(0, 10),
        time: nowTime,
        location: `${startLocation} ~ (주행 ${manualDefectDistance.toFixed(1)}m 지점)`,
        imageUrl: displayImageUrl,
        reportId,
        confidence: 100,
        aiModel: '수동 검출 (현장 작업자 직접 기록)',
        status: '검토 필요',
        coordinates: '37.4624, 126.8268',
        camId: 'SW-LIVE-01',
        originalImageId: `#MANUAL_${Date.now().toString().slice(-5)}`,
        recommendation: `작업자 현장 점검 소견: ${manualDefectType} 관찰됨. 즉각 정밀 내시 및 기계적 보강 판단 필요.`,
        timeline: [
          {
            id: `${newId}-BEFORE`, time: nowTime, title: '이상 징후 접근',
            desc: '작업자가 이상 징후를 확인하여 감속 후 화면을 관찰했습니다.',
            distance: Math.max(0, manualDefectDistance - 0.4), iconType: 'normal',
          },
          {
            id: `${newId}-CAPTURE`, time: nowTime, title: `${manualDefectType} 수동 포착`,
            desc: '현재 화면을 결함 이미지로 기록했습니다.',
            distance: manualDefectDistance, iconType: 'capture',
          },
          {
            id: `${newId}-AFTER`, time: nowTime, title: '결함 구간 통과',
            desc: '결함 기록을 마치고 안전 경로로 탐사 주행을 이어갔습니다.',
            distance: manualDefectDistance + 0.4, iconType: 'normal',
          },
        ],
      };

      setDefects((previous) => [manualItem, ...previous]);
      addLog(`[수동결함 추가] ${manualDefectType} 검측 (${manualDefectDistance.toFixed(2)}m) - 작업자 직접 입력`, 'alert');
      if (agentMission.state === 'RUNNING' && agentMission.missionId) {
        try {
          await reportProgressDefect(fetch, {
            eventId: `defect:${String(newId).replace(/[^A-Za-z0-9._:-]/g, '_')}`,
            missionId: agentMission.missionId,
            defectType: getApiDefectType(manualDefectType),
            confidence: 1,
            locationM: manualDefectDistance,
          }, import.meta.env);
        } catch (error) {
          addLog(`Qwen 결함 안내 실패: ${error instanceof Error ? error.message : '응답 없음'}`, 'warn');
        }
      }
      if (!USE_MOCK && manualCaptureUrl.startsWith('blob:')) {
        URL.revokeObjectURL(manualCaptureUrl);
      }
      setManualCaptureBlob(null);
      setManualCaptureUrl('');
      setShowManualAddPopup(false);
    } catch (error) {
      addLog(`결함 저장 실패: ${error instanceof Error ? error.message : '응답 없음'}`, 'alert');
    }
  };

  const handleCloseManualDefect = () => {
    if (manualCaptureUrl.startsWith('blob:')) {
      URL.revokeObjectURL(manualCaptureUrl);
    }
    setManualCaptureBlob(null);
    setManualCaptureUrl('');
    setShowManualAddPopup(false);
  };

  const currentDefects = status === '점검 전' ? [] : defects;

  const updateLiveInspection = (id: string, changes: Partial<InspectionItem>) => {
    setDefects((prev) => prev.map((item) => item.id === id ? { ...item, ...changes } : item));
    onUpdateInspection(id, changes);
  };

  // Dynamic pagination calculation
  const totalPages = Math.ceil(currentDefects.length / 3) || 1;
  const paginatedItems = currentDefects.slice((defectPage - 1) * 3, defectPage * 3);

  // Auto clamp page index
  useEffect(() => {
    if (defectPage > totalPages) {
      setDefectPage(totalPages);
    }
  }, [currentDefects.length, totalPages]);

  // Compute live statistics counts
  const typeCounts = currentDefects.reduce((acc, curr) => {
    let key = curr.defectType;
    if (key.includes('이음')) key = '이음부';
    else if (key.includes('균열')) key = '균열';
    else if (key.includes('퇴적')) key = '퇴적물';
    else if (key.includes('파손')) key = '파손';
    else if (key.includes('뿌리')) key = '뿌리침투';
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {} as Record<string, number>);

  const inspectionProgress = Math.min(100, Math.max(0, (distance / 120.0) * 100));
  const explicitInclinePercent = ultrasonicObservation?.inclinePercent;
  const pitchDegrees = ultrasonicObservation?.pitchDegrees
    ?? ultrasonicObservation?.pitch
    ?? ultrasonicObservation?.imuReading?.pitch
    ?? ultrasonicObservation?.imu_reading?.pitch;
  const inclinePercent = Number.isFinite(explicitInclinePercent)
    ? Number(explicitInclinePercent)
    : Number.isFinite(pitchDegrees)
      ? Math.tan((Number(pitchDegrees) * Math.PI) / 180) * 100
      : 0;

  return (
    <div className="flex flex-col lg:flex-row h-[calc(100vh-72px)] overflow-hidden bg-[#f9f9ff]">

      {/* 1. LEFT SIDEBAR: 실시간 점검 */}
      <aside className="w-full lg:w-[280px] bg-[#1e2433] text-white flex flex-col justify-between shrink-0 border-r border-[#151a26] shadow-xl">
        <div className="p-5 flex-1 overflow-y-auto space-y-6">
          <div className="flex items-center space-x-2 pb-4 mb-4 border-b border-[#2d3548]">
            <Radio className="w-5 h-5 text-[#4e81ee] animate-pulse" />
            <h2 className="text-[16px] font-black tracking-tight text-white">실시간 점검</h2>
          </div>

          {/* Parameters Table */}
          <div className="bg-[#151a26] rounded-lg border border-[#2d3548] overflow-hidden shadow-inner text-xs font-sans">
            <table className="w-full text-left">
              <tbody>
                <tr className="border-b border-[#2d3548]/50">
                  <td className="py-3 px-3 text-[#9ca3af] font-bold">점검 구간</td>
                  <td className="py-3 px-3 text-white font-bold text-right truncate max-w-[120px]" title={status === '점검 전' ? '정보 없음' : inspectionSection}>
                    {status === '점검 전' ? '정보 없음' : inspectionSection}
                  </td>
                </tr>
                <tr className="border-b border-[#2d3548]/50">
                  <td className="py-3 px-3 text-[#9ca3af] font-bold">관로 종류</td>
                  <td className="py-3 px-3 text-white font-sans font-bold text-right">
                    {status === '점검 전' ? '정보 없음' : pipeType}
                  </td>
                </tr>
                <tr className="border-b border-[#2d3548]/50">
                  <td className="py-3 px-3 text-[#9ca3af] font-bold">점검 방향</td>
                  <td className="py-3 px-3 text-white font-bold text-right">
                    {status === '점검 전' ? '정보 없음' : inspectionDirection}
                  </td>
                </tr>
                <tr className="border-b border-[#2d3548]/50">
                  <td className="py-3 px-3 text-[#9ca3af] font-bold">시작 지점</td>
                  <td className="py-3 px-3 text-white font-sans font-bold text-right">
                    {status === '점검 전' ? '정보 없음' : startLocation}
                  </td>
                </tr>
                <tr className="border-b border-[#2d3548]/50">
                  <td className="py-3 px-3 text-[#9ca3af] font-bold">실행 시간</td>
                  <td className="py-3 px-3 text-white font-sans font-bold text-right">
                    {status === '점검 전' ? '정보 없음' : missionActive ? '관제 중' : '임무 종료됨'}
                  </td>
                </tr>
                <tr className="border-b border-[#2d3548]/50">
                  <td className="py-3 px-3 text-[#9ca3af] font-bold">점검 시작</td>
                  <td className="py-3 px-3 text-white font-sans text-xs font-bold text-right">
                    {status === '점검 전' ? '정보 없음' : inspectionStartTime}
                  </td>
                </tr>
                <tr>
                  <td className="py-3 px-3 text-[#9ca3af] font-bold">점검 진행률</td>
                  <td className={`py-3 px-3 text-right text-xs font-black ${
                    status === '점검 전' ? 'font-sans text-white' : 'font-mono text-[#6ea0ff]'
                  }`}>
                    {status === '점검 전' ? '정보 없음' : `${inspectionProgress.toFixed(1)}%`}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          {/* Controls */}
          <div className="space-y-3 pt-2">
            {status === '점검 전' && (
              <button
                onClick={() => setShowSetupPopup(true)}
                className="flex items-center justify-center space-x-2 py-4 w-full bg-[#00875a] hover:bg-[#006644] text-white rounded-xl text-sm font-black transition-all shadow-md hover:shadow-lg cursor-pointer border border-[#005c3d]"
              >
                <Play className="w-4 h-4 fill-current shrink-0" />
                <span>점검 시작</span>
              </button>
            )}
            {missionActive && (
              <div className="space-y-2.5">
                <div className="grid grid-cols-2 gap-2.5">
                  <button
                    onClick={handleToggleDrive}
                    disabled
                    title="모터 안전 제어 API 연결 후 활성화됩니다."
                    className={`flex items-center justify-center space-x-1.5 py-3 text-white rounded-lg text-xs font-bold transition-all shadow-md hover:shadow-lg cursor-pointer border ${
                      status === '점검 중'
                        ? 'bg-[#ba1a1a] hover:bg-[#93000a] border-[#a21212]'
                        : 'bg-[#00875a] hover:bg-[#006644] border-[#005c3d] disabled:opacity-50'
                    }`}
                  >
                    {status === '점검 중' ? (
                      // Circle Icon (동그라미)
                      <span className="w-2.5 h-2.5 rounded-full bg-white border border-white/20 mr-1 shrink-0 animate-ping" />
                    ) : (
                      // Triangle Icon (세모)
                      <Play className="w-3.5 h-3.5 fill-current shrink-0 mr-1" />
                    )}
                    <span>주행 미연동</span>
                  </button>

                  <button
                    onClick={handleReturn}
                    disabled
                    title="복귀 제어는 아직 활성화되지 않았습니다."
                    className="flex items-center justify-center space-x-1.5 py-3 bg-[#374151] hover:bg-[#4b5563] text-[#e5e7eb] disabled:opacity-50 rounded-lg text-xs font-bold transition-all border border-[#2e3748] cursor-pointer"
                  >
                    <RotateCcw className="w-3.5 h-3.5 mr-1" />
                    <span>로봇 복귀</span>
                  </button>
                </div>

                <button
                  onClick={handleOpenManualDefect}
                  disabled
                  title="결함 탐지 기능은 자율주행 안정화 이후 연결됩니다."
                  className="flex items-center justify-center space-x-1.5 py-3.5 w-full bg-[#0f458f] text-white border border-[#0a2f64] rounded-lg text-xs font-black transition-all shadow-md disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <Plus className="w-3.5 h-3.5 mr-0.5" />
                  <span>결함 추가 미연동</span>
                </button>

                <button
                  onClick={handleEndInspection}
                  className="flex items-center justify-center space-x-1.5 py-2 w-full text-[#ff6b6b] hover:text-[#ff3b3b] font-bold text-xs transition-colors mt-1 cursor-pointer"
                >
                  <Ban className="w-3.5 h-3.5 mr-0.5" />
                  <span>점검 종료</span>
                </button>
              </div>
            )}
            {missionTerminal && (
              <button
                onClick={handleResetMission}
                title="종료된 임무를 초기화하고 새로운 탐사를 준비합니다."
                className="flex items-center justify-center space-x-2 py-4 w-full bg-[#00875a] hover:bg-[#006644] text-white rounded-xl text-sm font-black border border-[#005c3d] cursor-pointer transition-all"
              >
                <RefreshCw className="w-4 h-4" />
                <span>새로운 탐사 시작</span>
              </button>
            )}

          </div>

          {/* Hardware status */}
          <div className="space-y-3 pt-4 border-t border-[#2d3548]">
            <div className="flex items-center justify-between text-xs bg-[#151a26] py-3.5 px-4 rounded-lg border border-[#2d3548]/30">
              <span className="text-[#9ca3af] font-medium flex items-center gap-1.5">
                <span className="flex h-2.5 w-2.5 relative">
                  {cameraConnected && <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>}
                  <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${cameraConnected ? 'bg-emerald-500' : 'bg-red-500'}`}></span>
                </span>
                원본 카메라 (Pi)
              </span>
              <span className={`font-bold font-sans ${cameraConnected ? 'text-emerald-400' : 'text-red-400'}`}>
                {cameraConnected ? cameraName : '카메라 오프라인'}
              </span>
            </div>

            <div className="flex items-center justify-between text-xs bg-[#151a26] py-3.5 px-4 rounded-lg border border-[#2d3548]/30">
              <span className="text-[#9ca3af] font-medium flex items-center gap-1.5">
                <Wifi className="w-4 h-4 text-[#4e81ee]" />
                YOLO 주석 스트림
              </span>
              <span className={`font-bold font-sans ${yoloStreamConnected ? 'text-[#4e81ee]' : 'text-red-400'}`}>
                {yoloStreamConnected ? '영상 연결됨' : '연결 끊김'}
              </span>
            </div>
          </div>
        </div>

        {/* System shutdown and Brand signature */}
        <div className="p-4 bg-[#151a26] border-t border-[#1a202d]">
          <button
            onClick={handleSystemShutdown}
            disabled
            title="실제 시스템 종료 API가 연결되지 않았습니다."
            className="w-full py-2.5 bg-[#ba1a1a] text-white rounded-lg text-xs font-bold transition-all flex items-center justify-center space-x-1.5 shadow-sm border border-[#8f1010] disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Power className="w-3.5 h-3.5" />
            <span>시스템 종료 미연동</span>
          </button>
        </div>
      </aside>

      {/* 2. CENTER PANEL: CCTV Video Feed, Stats Rows, and Recent Defect Table */}
      <main className="flex-1 p-6 overflow-y-auto space-y-6 flex flex-col justify-between">

        <div className="space-y-4">
          {/* Header row with reactive status badge */}
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-black text-[#1a1b20] tracking-tight">
              하수관로 실시간 점검
            </h2>

            <div className="flex items-center space-x-2">
              {status === '점검 중' && (
                <span className="inline-flex items-center gap-1 px-3 py-1 bg-emerald-50 text-emerald-700 font-bold text-xs rounded-md border border-emerald-200">
                  <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                  점검 중
                </span>
              )}
              {status === '점검 중단' && (
                <span className="inline-flex items-center gap-1 px-3 py-1 bg-amber-50 text-amber-700 font-bold text-xs rounded-md border border-amber-200">
                  <span className="w-2 h-2 rounded-full bg-amber-500" />
                  점검 중단
                </span>
              )}
              {status === '복귀 중' && (
                <span className="inline-flex items-center gap-1 px-3 py-1 bg-purple-50 text-purple-700 font-bold text-xs rounded-md border border-purple-200">
                  <span className="w-2 h-2 rounded-full bg-purple-500 animate-spin" />
                  복귀 중
                </span>
              )}
              {status === '복귀 완료' && (
                <span className="inline-flex items-center gap-1 px-3 py-1 bg-teal-50 text-teal-700 font-bold text-xs rounded-md border border-teal-200">
                  <span className="w-2 h-2 rounded-full bg-teal-500" />
                  복귀 완료
                </span>
              )}
              {status === '점검 전' && (
                <span className="inline-flex items-center gap-1 px-3 py-1 bg-slate-100 text-slate-600 font-bold text-xs rounded-md border border-slate-200">
                  <span className="w-2 h-2 rounded-full bg-slate-400" />
                  점검 전
                </span>
              )}
              {status === '완료' && (
                <span className="inline-flex items-center gap-1 px-3 py-1 bg-emerald-50 text-emerald-700 font-bold text-xs rounded-md border border-emerald-200">
                  <CheckCircle2 className="w-3.5 h-3.5" /> 완료
                </span>
              )}
              {status === '막힘 종료' && (
                <span className="inline-flex items-center gap-1 px-3 py-1 bg-red-50 text-red-700 font-bold text-xs rounded-md border border-red-200">
                  <AlertTriangle className="w-3.5 h-3.5" /> 막힘 · 임무 종료
                </span>
              )}
              {missionTerminal && (status === '긴급 정지' || status === '오류 종료') && (
                <span className="inline-flex items-center gap-1 px-3 py-1 bg-red-50 text-red-700 font-bold text-xs rounded-md border border-red-200">
                  <AlertTriangle className="w-3.5 h-3.5" /> {status}
                </span>
              )}
            </div>
          </div>

          {/* Main CCTV Player */}
          <div className="relative aspect-video w-full bg-[#121318] rounded-xl overflow-hidden border border-[#c3c6d3] shadow-lg group">
            <img
              ref={imageRef}
              key={streamVersion}
              src={YOLO_STREAM_URL}
              crossOrigin="anonymous"
              alt="YOLO 장애물 탐지 영상"
              className="w-full h-full object-contain select-none"
              onLoad={() => setYoloStreamConnected(true)}
              onError={() => {
                setYoloStreamConnected(false);
                window.setTimeout(() => setStreamVersion(Date.now()), 2000);
              }}
            />

            {!yoloStreamConnected && (
              <div className="absolute inset-0 flex flex-col items-center justify-center bg-[#121318] text-white">
                <RefreshCw className="w-8 h-8 mb-3 text-[#4e81ee] animate-spin" />
                <p className="text-sm font-black">YOLO 주석 스트림 연결 중</p>
                <p className="mt-1 text-[11px] text-slate-400">AI Agent · /api/vision/yolo-stream.mjpg</p>
              </div>
            )}

            <div className="absolute top-5 left-5 text-white text-[11px] font-black bg-black/55 px-2.5 py-1.5 rounded-sm pointer-events-none">
              YOLO 장애물 탐지 영상
            </div>

            <div className="absolute top-5 right-5 text-white text-[11px] font-mono text-right leading-relaxed bg-black/45 px-2.5 py-1.5 rounded-sm pointer-events-none">
              <p className="font-bold">{dateStr}</p>
              <p className="tracking-wider text-sm text-[#00ffcc]">{timeStr}</p>
            </div>

            <div className="absolute bottom-5 left-5 text-white text-xs font-mono leading-relaxed bg-black/50 p-3.5 rounded-md border-l-4 border-[#0f458f] pointer-events-none max-w-[280px]">
              <p className="font-black text-xs text-[#ffd54f] truncate">{status === '점검 전' ? '정보 없음' : inspectionSection}</p>
              <p className="text-[#a5b4fc] text-[10px] mt-0.5 truncate">
                {status === '점검 전' ? '정보 없음' : `${startLocation} ➔ ${inspectionDirection === '정방향' ? 'MH-22439' : 'MH-22905'}`}
              </p>
              <div className="flex items-center space-x-2.5 mt-1.5 pt-1.5 border-t border-white/20 text-[10px]">
                <span>DIR: {status === '점검 전' ? '정보 없음' : inspectionDirection}</span>
              </div>
            </div>

            <div className="absolute bottom-5 right-5 text-white text-right text-[10px] font-mono leading-relaxed bg-black/50 p-3.5 rounded-md pointer-events-none">
              <p>{status === '점검 전' ? '정보 없음' : pipeType} / 정면</p>
              <p className="text-[#00ffcc] font-bold text-xs mt-1">SPD: {speed.toFixed(2)} m/s</p>
              <p className="text-[#00ffcc] font-bold text-xs">SLOPE: {inclinePercent.toFixed(2)}%</p>
            </div>

            <div className="absolute inset-0 pointer-events-none border-2 border-[#0f458f]/10" />

          </div>

          {/* Three Info Cards below CCTV */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">

            <div className="bg-white p-4 rounded-xl border border-[#c3c6d3] shadow-2xs space-y-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-bold text-[#737782]">진행 상황 요약</span>
                <span className="inline-flex shrink-0 items-center gap-1 text-[10px] font-bold text-[#0f458f]">
                  <span className={`h-1.5 w-1.5 rounded-full ${progressSummaryTone === 'ready' ? 'bg-emerald-500' : progressSummaryTone === 'pending' ? 'bg-amber-500 animate-pulse' : progressSummaryTone === 'error' ? 'bg-red-500' : 'bg-slate-400'}`} />
                  Qwen2.5-VL
                </span>
              </div>
              <div className={`flex items-center gap-2 rounded-lg px-3 py-2.5 ${progressSummaryTone === 'error' ? 'bg-red-50' : progressSummaryTone === 'pending' ? 'bg-amber-50' : 'bg-[#f3f3fa]'}`}>
                <Activity className={`h-4 w-4 shrink-0 ${progressSummaryTone === 'error' ? 'text-red-600' : progressSummaryTone === 'pending' ? 'text-amber-600' : 'text-[#0f458f]'}`} />
                <p className="min-w-0 flex-1 text-[11px] font-bold leading-relaxed text-[#434751]" title={progressSummary}>
                  {progressSummary}
                </p>
              </div>
              <p className="text-right font-mono text-[10px] text-[#737782]">
                최근 업데이트 {progressSummaryTime}
              </p>
            </div>

            <div className="bg-white p-4 rounded-xl border border-[#c3c6d3] shadow-2xs space-y-2">
              <div className="flex justify-between items-center">
                <span className="text-xs font-bold text-[#737782]">결함 통계</span>
                <span className="text-[14px] font-bold text-[#1a1b20]">
                  <span className="text-red-600 font-extrabold text-base mr-1">{currentDefects.length}</span>건 발견됨
                </span>
              </div>

              <div className="flex flex-wrap gap-1.5 pt-1">
                {currentDefects.length === 0 ? (
                  <span className="text-[11px] text-[#737782] font-semibold italic">현재 검출된 결함 없음</span>
                ) : (
                  Object.entries(typeCounts).map(([type, count]) => (
                    <span key={type} className="px-2 py-1 bg-[#f3f3fa] text-[11px] font-bold text-[#434751] rounded-md border border-slate-200">
                      {type} <span className="text-[#0f458f] font-extrabold">{count}</span>
                    </span>
                  ))
                )}
              </div>
            </div>

            <div className="bg-white p-4 rounded-xl border border-[#c3c6d3] shadow-2xs">
              <span className="text-xs font-bold text-[#737782] block mb-2">AI 주행 및 장비</span>

              <div className="space-y-1.5 text-xs">
                <div className="rounded-lg border border-[#d8dbe8] bg-[#f8f9ff] p-2 mb-2 space-y-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <label htmlFor="navigation-version" className="font-black text-[#434751]">주행 버전 선택</label>
                    <select
                      id="navigation-version"
                      value={navigationStatus.activeVersion}
                      onChange={(event) => handleNavigationVersionChange(event.target.value as 'v1' | 'v2')}
                      disabled={!navigationSelectable || navigationChanging}
                      className="rounded-md border border-[#c3c6d3] bg-white px-2 py-1 font-bold text-[#1a1b20] disabled:opacity-50"
                    >
                      <option value="v1" disabled={!navigationStatus.v1Available}>V1 규칙 기반</option>
                      <option value="v2" disabled={!navigationStatus.v2Available}>V2 AI 판단</option>
                    </select>
                  </div>
                  <p className="text-[10px] font-bold text-[#737782]">
                    {navigationStatus.activeVersion === 'v2'
                      ? 'V2: Neural Policy 좌·우 회피 · 장애물 통과 없음'
                      : 'V1: LocalFreeSpacePlanner 규칙 기반 회피'}
                  </p>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-[#737782] font-medium">현재 인식 Backend</span>
                  <span className="font-mono font-bold text-[#0f458f] truncate" title={driveStatus.perceptionModel}>{driveStatus.perceptionModel}</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-[#737782] font-medium">현재 빠른 판단</span>
                  <span className="font-mono font-bold text-[#1a1b20] truncate" title={driveStatus.decisionModel}>{driveStatus.decisionModel}</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-[#737782] font-medium">현재 인식 모델</span>
                  <span className={`font-mono font-bold truncate ${modelStatus.perception.downloaded ? 'text-emerald-600' : 'text-red-600'}`} title={modelStatus.perception.note}>{modelStatus.perception.name} · {modelStatus.perception.downloaded ? '다운로드됨' : '없음'}</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-[#737782] font-medium">이벤트 판단 모델</span>
                  <span className={`font-mono font-bold truncate ${modelStatus.decision.downloaded ? 'text-emerald-600' : 'text-red-600'}`} title={modelStatus.decision.note}>{modelStatus.decision.name} · {modelStatus.decision.loaded ? '실행 중' : modelStatus.decision.downloaded ? '대기' : '없음'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#737782] font-medium">현재 주행 명령</span>
                  <span className="font-bold text-[#1a1b20]">{driveUi.steeringLabel}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#737782] font-medium">현재 좌·우 명령값</span>
                  <span className="font-mono font-bold text-[#1a1b20]">{driveUi.wheelLabel}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#737782] font-medium">Safety</span>
                  <span className={`font-bold ${driveStatus.latestCommand?.safetyApproved ? 'text-emerald-600' : 'text-amber-600'}`}>{driveUi.safetyLabel}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#737782] font-medium">원본 카메라 상태 응답</span>
                  <span className="font-mono font-bold text-[#0f458f]">{latency === null ? '확인 중' : `${latency}ms`}</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-[#737782] font-medium">YOLO 모델</span>
                  <span className="font-mono font-bold text-[#1a1b20] truncate" title={yoloUi.modelLabel}>{yoloUi.modelLabel}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#737782] font-medium">YOLO 검출</span>
                  <span className="font-mono font-bold text-[#1a1b20]">{yoloUi.detectionsLabel}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#737782] font-medium">YOLO 추론 지연</span>
                  <span className="font-mono font-bold text-[#1a1b20]">{yoloUi.latencyLabel}</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-[#737782] font-medium">YOLO 오류</span>
                  <span className={`font-mono font-bold truncate ${yoloStatus?.error ? 'text-red-600' : 'text-[#1a1b20]'}`} title={yoloUi.errorLabel}>{yoloUi.errorLabel}</span>
                </div>
              </div>
            </div>

          </div>
        </div>

        {/* 3. 최근 발견 결함 Table (Paginated 1, 2, 3) */}
        <div className="bg-white rounded-xl border border-[#c3c6d3] p-4 shadow-sm mt-4">
          <h3 className="text-sm font-black text-[#1a1b20] mb-3 flex items-center justify-between border-l-4 border-[#0f458f] pl-2">
            <span className="flex items-center gap-1.5">
              <Sliders className="w-4.5 h-4.5 text-[#0f458f]" />
              <span>최근 발견 결함</span>
            </span>
            <span className="text-xs font-bold text-[#737782]">AI 결함 탐지 연동 대기</span>
          </h3>

          <div className="overflow-x-auto">
            <table className="w-full text-xs text-left">
              <thead>
                <tr className="bg-[#f3f3fa] border-b border-[#c3c6d3] text-[#737782] h-10">
                  <th className="px-4 font-bold text-[#1a1b20]">번호</th>
                  <th className="px-4 font-bold text-[#1a1b20]">발견 시각</th>
                  <th className="px-4 font-bold text-[#1a1b20]">위치</th>
                  <th className="px-4 font-bold text-[#1a1b20]">종류</th>
                  <th className="px-4 font-bold text-[#1a1b20]">등급</th>
                  <th className="px-4 font-bold text-[#1a1b20]">상태</th>
                  <th className="px-4 font-bold text-[#1a1b20] text-center">상세</th>
                </tr>
              </thead>
              <tbody>
                {paginatedItems.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-8 text-center text-[#737782] font-semibold bg-white">
                      검출된 결함이 없거나 점검 시작 전입니다. (결함 통계 0건)
                    </td>
                  </tr>
                ) : (
                  paginatedItems.map((item, idx) => {
                    const globalIdx = (defectPage - 1) * 3 + idx + 1;
                    const isCritical = item.severity === 'critical';

                    // Calculate dynamic distance for list
                    const displayDist = item.id === 'INSP-001' ? '41.70 m' : item.id === 'INSP-002' ? '35.42 m' : item.id === 'INSP-003' ? '27.15 m' : `${(30 + globalIdx * 8.4).toFixed(2)} m`;

                    return (
                      <tr key={item.id} className="border-b border-[#ededf4] hover:bg-[#f9f9ff] h-11 transition-colors">
                        <td className="px-4 font-mono font-bold text-[#434751]">{globalIdx}</td>
                        <td className="px-4 font-mono text-[#737782]">{item.time}</td>
                        <td className="px-4 font-mono font-extrabold text-[#1a1b20]">{displayDist}</td>
                        <td className="px-4 font-bold text-[#1a1b20]">{item.defectType}</td>
                        <td className={`px-4 font-black ${isCritical ? 'text-[#ba1a1a]' : 'text-slate-600'}`}>
                          {isCritical ? '1등급' : '2등급'}
                        </td>
                        <td className="px-4">
                          <select
                            value={STATUS_OPTIONS.includes(item.status as typeof STATUS_OPTIONS[number]) ? item.status : '검토 필요'}
                            onChange={(event) => updateLiveInspection(item.id, { status: event.target.value })}
                            className="px-2 py-1 text-[10px] font-bold rounded-md border border-[#c3c6d3] bg-white text-[#434751] focus:outline-none focus:ring-2 focus:ring-[#0f458f]"
                          >
                            {STATUS_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}
                          </select>
                        </td>
                        <td className="px-4 text-center">
                          <button
                            onClick={() => onViewDefect(item)}
                            className="p-1 text-[#737782] hover:text-[#0f458f] transition-colors inline-flex justify-center items-center cursor-pointer"
                            title="상세 진단 팝업 열기"
                          >
                            <Eye className="w-4 h-4 stroke-[2.5]" />
                          </button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          {/* Center defect table pagination selector (1, 2, 3) */}
          <div className="flex justify-between items-center mt-3 pt-3 border-t border-slate-100">
            <span className="text-[11px] text-[#737782] font-semibold">
              전체 {currentDefects.length}건 중 {currentDefects.length === 0 ? 0 : (defectPage - 1) * 3 + 1}-{Math.min(currentDefects.length, defectPage * 3)}건 표시
            </span>
            <div className="flex space-x-1.5 items-center">
              <button
                onClick={() => setDefectPage(1)}
                disabled={defectPage === 1}
                className="w-7 h-7 flex items-center justify-center rounded-md font-bold text-xs border bg-white text-[#434751] border-[#c3c6d3] hover:bg-[#f3f3fa] disabled:opacity-40 cursor-pointer disabled:cursor-not-allowed"
                title="첫 페이지"
              >
                «
              </button>
              <button
                onClick={() => setDefectPage((p) => Math.max(1, p - 1))}
                disabled={defectPage === 1}
                className="w-7 h-7 flex items-center justify-center rounded-md font-bold text-xs border bg-white text-[#434751] border-[#c3c6d3] hover:bg-[#f3f3fa] disabled:opacity-40 cursor-pointer disabled:cursor-not-allowed"
                title="이전 페이지"
              >
                ‹
              </button>
              {Array.from({ length: totalPages }).map((_, i) => {
                const p = i + 1;
                return (
                  <button
                    key={p}
                    onClick={() => setDefectPage(p)}
                    className={`w-7 h-7 flex items-center justify-center rounded-md font-bold text-xs border transition-all cursor-pointer ${
                      defectPage === p
                        ? 'bg-[#0f458f] text-white border-[#0f458f]'
                        : 'bg-white text-[#434751] border-[#c3c6d3] hover:bg-[#f3f3fa]'
                    }`}
                  >
                    {p}
                  </button>
                );
              })}
              <button
                onClick={() => setDefectPage((p) => Math.min(totalPages, p + 1))}
                disabled={defectPage === totalPages}
                className="w-7 h-7 flex items-center justify-center rounded-md font-bold text-xs border bg-white text-[#434751] border-[#c3c6d3] hover:bg-[#f3f3fa] disabled:opacity-40 cursor-pointer disabled:cursor-not-allowed"
                title="다음 페이지"
              >
                ›
              </button>
              <button
                onClick={() => setDefectPage(totalPages)}
                disabled={defectPage === totalPages}
                className="w-7 h-7 flex items-center justify-center rounded-md font-bold text-xs border bg-white text-[#434751] border-[#c3c6d3] hover:bg-[#f3f3fa] disabled:opacity-40 cursor-pointer disabled:cursor-not-allowed"
                title="마지막 페이지"
              >
                »
              </button>
            </div>
          </div>
        </div>

      </main>

      {/* 3. RIGHT SIDEBAR: separated live sensor, perception, and decision telemetry. */}
      <aside className="w-full lg:w-[340px] bg-white border-l border-[#c3c6d3] p-4 shrink-0 flex flex-col">
        <div className="space-y-4 flex-1 overflow-y-auto">
          <div className="flex items-center justify-between pb-3 border-b border-[#e2e2e9]">
            <div className="flex items-center space-x-1.5">
              <Activity className="w-4.5 h-4.5 text-[#0f458f]" />
              <h2 className="text-[14px] font-black text-[#1a1b20]">AI 실시간 상태</h2>
            </div>
            <span className="font-mono text-[10px] font-bold text-[#737782]">{telemetryTime}</span>
          </div>

          <section className="rounded-xl border border-[#c3c6d3] bg-[#f8f9ff] p-3 space-y-2" aria-label="초음파 상태">
            <div className="flex items-center justify-between">
              <h3 className="flex items-center gap-1.5 text-xs font-black text-[#1a1b20]">
                <Radio className="w-4 h-4 text-[#0f458f]" /> 초음파 상태
              </h3>
              <span className={`text-[10px] font-black ${ultrasonicUi.tone === 'alert' ? 'text-red-600' : ultrasonicUi.tone === 'warn' ? 'text-amber-600' : ultrasonicUi.tone === 'success' ? 'text-emerald-600' : 'text-[#737782]'}`}>
                {ultrasonicUi.stateLabel}
              </span>
            </div>
            <div className="flex items-end justify-between">
              <span className="text-[10px] font-bold text-[#737782]">전방 실측 거리</span>
              <span className="font-mono text-base font-black text-[#1a1b20]">{ultrasonicUi.distanceLabel}</span>
            </div>
            <p className="text-[10px] leading-relaxed text-[#737782]">초음파는 원통형 관로에서 속도 보조 telemetry로만 사용하며 거리만으로 정지하지 않습니다.</p>
          </section>

          <section className="rounded-xl border border-[#c3c6d3] bg-[#f8f9ff] p-3 space-y-2" aria-label="장애물 인식 상태">
            <div className="flex items-center justify-between">
              <h3 className="flex items-center gap-1.5 text-xs font-black text-[#1a1b20]">
                <Eye className="w-4 h-4 text-[#0f458f]" /> 장애물 인식 상태
              </h3>
              <span className={`text-[10px] font-black ${obstacleUi.tone === 'warn' ? 'text-amber-600' : obstacleUi.tone === 'success' ? 'text-emerald-600' : 'text-[#737782]'}`}>
                {obstacleUi.stateLabel}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-2 text-[10px]">
              <div className="rounded-lg bg-white border border-[#e2e2e9] p-2">
                <span className="block font-bold text-[#737782]">막힘 확률</span>
                <span className="font-mono text-sm font-black text-[#1a1b20]">{obstacleUi.blockedLabel}</span>
              </div>
              <div className="rounded-lg bg-white border border-[#e2e2e9] p-2">
                <span className="block font-bold text-[#737782]">AI 신뢰도</span>
                <span className="font-mono text-sm font-black text-[#1a1b20]">{obstacleUi.confidenceLabel}</span>
              </div>
            </div>
            <p className="font-mono text-[10px] font-bold text-[#434751]">{obstacleUi.freeSpaceLabel}</p>
            <div className="flex justify-between text-[10px] text-[#737782]">
              <span>{obstaclePerception?.modelName || 'Depth 모델 대기'}</span>
              <span className="font-mono">추론 {obstacleUi.latencyLabel}</span>
            </div>
          </section>

          <section className="rounded-xl border border-[#c3c6d3] bg-[#f8f9ff] p-3 space-y-2" aria-label="AI 회피 판단">
            <div className="flex items-center justify-between">
              <h3 className="flex items-center gap-1.5 text-xs font-black text-[#1a1b20]">
                <Sliders className="w-4 h-4 text-[#0f458f]" /> AI 회피 판단
              </h3>
              <span className={`text-[10px] font-black ${localPlan?.action === 'STOP' ? 'text-red-600' : (localPlan?.risk ?? 0) >= 0.70 ? 'text-amber-600' : 'text-emerald-600'}`}>
                {localPlan?.action || '계획 대기'}
              </span>
            </div>
            <div className="grid grid-cols-3 gap-1.5 text-center">
              <div className="rounded-lg bg-white border border-[#e2e2e9] p-1.5">
                <span className="block text-[9px] font-bold text-[#737782]">조향</span>
                <span className="font-mono text-xs font-black">{localPlan ? `${Math.round(localPlan.steering * 100)}%` : '대기'}</span>
              </div>
              <div className="rounded-lg bg-white border border-[#e2e2e9] p-1.5">
                <span className="block text-[9px] font-bold text-[#737782]">출력 목표</span>
                <span className="font-mono text-xs font-black">{localPlan ? `${Math.round(localPlan.throttle * 100)}%` : '대기'}</span>
              </div>
              <div className="rounded-lg bg-white border border-[#e2e2e9] p-1.5">
                <span className="block text-[9px] font-bold text-[#737782]">위험도</span>
                <span className="font-mono text-xs font-black">{localPlan ? `${Math.round(localPlan.risk * 100)}%` : '대기'}</span>
              </div>
            </div>
            <p className="text-[10px] font-bold leading-relaxed text-[#434751]">
              {localPlan ? `선택 후보 ${localPlan.selectedSector + 1}/9 · ${localPlan.reason}` : 'Depth AI 결과를 기다리고 있습니다.'}
            </p>
          </section>

          <section className="space-y-2" aria-label="AI 판단 로그">
            <div className="flex items-center justify-between border-b border-[#e2e2e9] pb-2">
              <h3 className="text-xs font-black text-[#1a1b20]">AI 판단 로그</h3>
              <span className="text-[10px] font-bold text-[#737782]">최근 {Math.min(logs.length, 30)}건</span>
            </div>
            <div className="relative border-l border-dashed border-[#c3c6d3] ml-2 pl-4 space-y-3">
              {logs.slice(0, 30).map((log, index) => {
                let dotBg = 'bg-blue-500';
                if (log.type === 'alert') dotBg = 'bg-red-600 shadow-[0_0_4px_#ef4444]';
                else if (log.type === 'warn') dotBg = 'bg-amber-500';
                else if (log.type === 'success') dotBg = 'bg-[#00e676] shadow-[0_0_4px_#00e676]';
                return (
                  <div key={`${log.time}-${index}`} className="relative">
                    <span className={`absolute -left-[20.5px] top-1 flex h-2.5 w-2.5 rounded-full border border-white ${dotBg}`} />
                    <p className={`font-mono text-[10px] font-extrabold ${log.type === 'alert' ? 'text-red-600' : log.type === 'warn' ? 'text-amber-500' : log.type === 'success' ? 'text-emerald-500' : 'text-blue-500'}`}>{log.time}</p>
                    <p className="text-xs font-bold text-[#434751] mt-0.5 leading-relaxed">{log.text}</p>
                  </div>
                );
              })}
            </div>
          </section>
        </div>
      </aside>

      {/* 4. Real-time Final Inspection Result Modal */}
      {showResultPopup && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs">
          <div className="bg-white rounded-2xl p-6 max-w-md w-full border border-[#c3c6d3] shadow-2xl space-y-5 animate-in fade-in zoom-in-95 duration-200">
            <div className={`flex items-center space-x-3 border-b border-slate-100 pb-3 ${resultUi.resultTone === 'danger' ? 'text-red-600' : 'text-emerald-600'}`}>
              {resultUi.resultTone === 'danger'
                ? <AlertTriangle className="w-6 h-6 stroke-[2.5]" />
                : <CheckCircle2 className="w-6 h-6 stroke-[2.5]" />}
              <h3 className="text-lg font-black text-[#1a1b20]">{resultUi.resultTitle}</h3>
            </div>

            <div className="space-y-3.5 text-xs">
              <p className="text-[#434751] leading-relaxed">
                {resultUi.resultMessage}
              </p>
              {agentMission.state === 'FAILED_BLOCKED' && agentMission.agentReason && (
                <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-red-800">
                  <span className="font-black">Agent 판단 근거:</span> {agentMission.agentReason}
                </div>
              )}

              <div className="bg-[#f3f3fa] rounded-xl p-4 border border-[#c3c6d3]/45 space-y-2.5 font-semibold">
                <div className="flex justify-between">
                  <span className="text-[#737782]">점검 구역</span>
                  <span className="text-[#1a1b20] font-bold max-w-[180px] truncate">{inspectionSection}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#737782]">최종 주행 거리</span>
                  <span className="text-[#0f458f] font-bold font-mono">{resultUi.distanceLabel}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[#737782]">결함 탐지</span>
                  <span className="text-[#1a1b20] font-bold">현재 개발 범위 제외</span>
                </div>
              </div>
            </div>

            <div className="flex gap-2.5 pt-2">
              <button
                onClick={handleResetMission}
                className="flex-1 py-3 bg-white hover:bg-slate-50 border border-[#c3c6d3] text-xs font-bold text-[#434751] rounded-lg transition-all text-center cursor-pointer"
              >
                새로운 탐사 시작
                </button>
                <button
                onClick={handleCreateReportAndReset}
                title="현재 임무 결과로 보고서를 생성합니다."
                className="flex-1 py-3 bg-[#0f458f] hover:bg-[#0a336c] text-xs font-black text-white rounded-lg transition-all shadow-md text-center cursor-pointer"
                >
                보고서 생성
                </button>
            </div>
          </div>
        </div>
      )}

      {/* 5. Inspection Setup Modal (점검 시작 팝업) */}
      {showSetupPopup && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs">
          <div className="bg-white rounded-2xl p-6 max-w-md w-full border border-[#c3c6d3] shadow-2xl space-y-4 animate-in fade-in zoom-in-95 duration-200">
            <div className="flex justify-between items-center border-b border-slate-100 pb-3">
              <h3 className="text-base font-black text-[#1a1b20] flex items-center gap-1.5">
                <Sliders className="w-5 h-5 text-[#0f458f]" />
                <span>점검 기본 설정</span>
              </h3>
              <button
                onClick={() => setShowSetupPopup(false)}
                className="p-1 text-[#737782] hover:text-[#1a1b20]"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-3.5 text-xs">
              <div className="space-y-1">
                <label className="block font-black text-[#434751]">점검 구간명</label>
                <input
                  type="text"
                  value={inspectionSection}
                  onChange={(e) => setInspectionSection(e.target.value)}
                  className="w-full px-3 py-2.5 bg-[#f3f3fa] border border-[#c3c6d3] rounded-lg font-bold text-[#1a1b20] focus:outline-none focus:ring-2 focus:ring-[#0f458f]"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="block font-black text-[#434751]">점검 로봇</label>
                  <select
                    value={selectedRobotId ?? ''}
                    onChange={(event) => setSelectedRobotId(Number(event.target.value))}
                    disabled={robots.length === 0}
                    className="w-full px-3 py-2.5 bg-[#f3f3fa] border border-[#c3c6d3] rounded-lg font-bold text-[#1a1b20] focus:outline-none focus:ring-2 focus:ring-[#0f458f] disabled:opacity-50"
                  >
                    {robots.length === 0 ? (
                      <option value="">등록된 로봇 없음</option>
                    ) : (
                      robots.map((robot) => (
                        <option key={robot.id} value={robot.id}>
                          {robot.name} ({robot.model})
                        </option>
                      ))
                    )}
                  </select>
                </div>
                <div className="space-y-1">
                  <label className="block font-black text-[#434751]">승인 운영자</label>
                  <select
                    value={selectedOperatorId ?? ''}
                    onChange={(event) => setSelectedOperatorId(Number(event.target.value))}
                    disabled={operators.length === 0}
                    className="w-full px-3 py-2.5 bg-[#f3f3fa] border border-[#c3c6d3] rounded-lg font-bold text-[#1a1b20] focus:outline-none focus:ring-2 focus:ring-[#0f458f] disabled:opacity-50"
                  >
                    {operators.length === 0 ? (
                      <option value="">등록된 운영자 없음</option>
                    ) : (
                      operators.map((operator) => (
                        <option key={operator.id} value={operator.id}>
                          {operator.name} ({operator.role})
                        </option>
                      ))
                    )}
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="block font-black text-[#434751]">관로 종류</label>
                  <input
                    type="text"
                    value={pipeType}
                    onChange={(e) => setPipeType(e.target.value)}
                    className="w-full px-3 py-2.5 bg-[#f3f3fa] border border-[#c3c6d3] rounded-lg font-bold text-[#1a1b20] focus:outline-none focus:ring-2 focus:ring-[#0f458f]"
                  />
                </div>

                <div className="space-y-1">
                  <label className="block font-black text-[#434751]">점검 방향</label>
                  <select
                    value={inspectionDirection}
                    onChange={(e) => setInspectionDirection(e.target.value)}
                    className="w-full px-3 py-2.5 bg-[#f3f3fa] border border-[#c3c6d3] rounded-lg font-bold text-[#1a1b20] focus:outline-none focus:ring-2 focus:ring-[#0f458f]"
                  >
                    <option value="정방향">정방향 (➔)</option>
                    <option value="역방향" disabled>역방향 (현재 미지원)</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="block font-black text-[#434751]">시작 지점 (맨홀)</label>
                  <input
                    type="text"
                    value={startLocation}
                    onChange={(e) => setStartLocation(e.target.value)}
                    className="w-full px-3 py-2.5 bg-[#f3f3fa] border border-[#c3c6d3] rounded-lg font-bold text-[#1a1b20] focus:outline-none focus:ring-2 focus:ring-[#0f458f]"
                  />
                </div>

                <div className="space-y-1">
                  <label className="block font-black text-[#434751]">설정 시간 (자동)</label>
                  <input
                    type="text"
                    readOnly
                    value={`${new Date().toISOString().slice(0, 10)} ${new Date().toTimeString().split(' ')[0]}`}
                    className="w-full px-3 py-2.5 bg-slate-100 border border-[#c3c6d3] rounded-lg font-bold text-[#737782] cursor-not-allowed select-none"
                  />
                </div>
              </div>
            </div>

            <div className="flex gap-2.5 pt-3 border-t border-slate-100">
              <button
                onClick={() => setShowSetupPopup(false)}
                className="flex-1 py-3 bg-white hover:bg-slate-50 border border-[#c3c6d3] text-xs font-bold text-[#434751] rounded-lg transition-all"
              >
                취소
              </button>
              <button
                onClick={handleStartInspection}
                disabled={!agentConnected}
                title={agentConnected ? 'AI Agent 관제 임무를 시작합니다.' : 'AI Agent 서버 연결 후 시작할 수 있습니다.'}
                className="flex-1 py-3 bg-[#0f458f] hover:bg-[#0a336c] text-xs font-black text-white rounded-lg transition-all shadow-md disabled:opacity-50 disabled:cursor-not-allowed"
              >
                설정 완료 및 Agent 점검 시작
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 6. Manual Defect Add Modal (수동 결함 추가 팝업) */}
      {showManualAddPopup && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs">
          <div className="bg-white rounded-2xl p-6 max-w-sm w-full border border-[#c3c6d3] shadow-2xl space-y-4 animate-in fade-in zoom-in-95 duration-200">
            <div className="flex justify-between items-center border-b border-slate-100 pb-3">
              <h3 className="text-base font-black text-[#1a1b20] flex items-center gap-1.5">
                <Plus className="w-5 h-5 text-[#0f458f]" />
                <span>수동 결함 직접 추가</span>
              </h3>
              <button
                onClick={handleCloseManualDefect}
                className="p-1 text-[#737782] hover:text-[#1a1b20]"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-3.5 text-xs">
              <div className="space-y-1">
                <label className="block font-black text-[#434751]">결함 종류 선택</label>
                <select
                  value={manualDefectType}
                  onChange={(e) => setManualDefectType(e.target.value)}
                  className="w-full px-3 py-2.5 bg-[#f3f3fa] border border-[#c3c6d3] rounded-lg font-bold text-[#1a1b20] focus:outline-none focus:ring-2 focus:ring-[#0f458f]"
                >
                  <option value="균열">균열</option>
                  <option value="이음부 결합">이음부 결합</option>
                  <option value="퇴적물">퇴적물</option>
                  <option value="이물질">이물질</option>
                  <option value="파손">파손</option>
                  <option value="수목 뿌리 침투">수목 뿌리 침투</option>
                </select>
              </div>

              <div className="space-y-1">
                <label className="block font-black text-[#434751]">결함 심각도 등급</label>
                <select
                  value={manualSeverity}
                  onChange={(e) => setManualSeverity(e.target.value)}
                  className="w-full px-3 py-2.5 bg-[#f3f3fa] border border-[#c3c6d3] rounded-lg font-bold text-[#1a1b20] focus:outline-none focus:ring-2 focus:ring-[#0f458f]"
                >
                  <option value="critical">1등급 (심각)</option>
                  <option value="warning">2등급 (주의)</option>
                </select>
              </div>

              <div className="space-y-1">
                <label className="block font-black text-[#434751]">현재 발견 위치</label>
                <input
                  type="text"
                  readOnly
                  value={`${manualDefectDistance.toFixed(2)} m (기록 시점 고정)`}
                  className="w-full px-3 py-2.5 bg-slate-100 border border-[#c3c6d3] rounded-lg font-bold text-[#737782] cursor-not-allowed select-none"
                />
              </div>
            </div>

            <div className="flex gap-2.5 pt-3 border-t border-slate-100">
              <button
                onClick={handleCloseManualDefect}
                className="flex-1 py-3 bg-white hover:bg-slate-50 border border-[#c3c6d3] text-xs font-bold text-[#434751] rounded-lg transition-all"
              >
                취소
              </button>
              <button
                onClick={() => void handleAddManualDefect()}
                className="flex-1 py-3 bg-[#0f458f] hover:bg-[#0a336c] text-xs font-black text-white rounded-lg transition-all shadow-md"
              >
                결함 추가 완료
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
