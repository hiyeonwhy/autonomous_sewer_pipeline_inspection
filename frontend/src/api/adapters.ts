import type {
  DefectSeverity,
  InspectionItem,
  InspectionLog,
  ReportMeta,
} from '../types';
import type {
  ApiDefect,
  ApiDriveLog,
  ApiReport,
  ApiSession,
} from '../types/api';
import { PLACEHOLDER_IMAGE, resolveMediaUrl } from './media';

export type UiSessionStatus =
  | '점검 전'
  | '점검 중'
  | '점검 중단'
  | '복귀 중'
  | '점검 완료';

export function getApiId(value: { id: number }): number {
  return value.id;
}

export function normalizeSeverity(severity: string): DefectSeverity {
  switch (severity.toLowerCase()) {
    case 'critical':
      return 'critical';
    case 'warning':
      return 'warning';
    case 'normal':
    default:
      return 'normal';
  }
}

export function normalizeSessionStatus(status: string): UiSessionStatus {
  switch (status.toLowerCase()) {
    case 'running':
      return '점검 중';
    case 'paused':
    case 'aborted':
      return '점검 중단';
    case 'returning':
      return '복귀 중';
    case 'completed':
      return '점검 완료';
    default:
      return '점검 전';
  }
}

export function getDefectTypeLabel(
  defectType: string,
): string {
  switch (defectType.trim().toUpperCase()) {
    case 'CRACK':
    case '균열':
      return '균열';
    case 'BLOCKAGE':
    case '막힘':
      return '막힘';
    case 'SURFACE_DAMAGE':
    case '표면손상':
      return '표면손상';
    case 'DEBRIS':
    case '퇴적물':
      return '퇴적물';
    case 'ROOT':
    case '뿌리 침투':
    case '수목 뿌리 침투':
      return '뿌리 침투';
    case 'OTHER':
    case '기타':
    default:
      return '기타';
  }
}

export function defectStatusToUi(
  reviewStatus: ApiDefect['review_status'],
  repairStatus: ApiDefect['repair_status'],
): string {
  if (repairStatus === 'COMPLETED') return '보수 완료';
  if (repairStatus === 'IN_PROGRESS') return '작업 예정';
  if (repairStatus === 'NOT_NEEDED') return '보수 불필요';
  return reviewStatus === 'REVIEWED' ? '검토 완료' : '검토 필요';
}

export function uiStatusToDefectUpdate(status: string): {
  review_status: 'PENDING' | 'REVIEWED';
  repair_status: 'BEFORE' | 'IN_PROGRESS' | 'COMPLETED' | 'NOT_NEEDED';
} {
  switch (status) {
    case '작업 예정':
    case '보수 중':
    case '담당자 배정':
      return { review_status: 'REVIEWED', repair_status: 'IN_PROGRESS' };
    case '보수 완료':
    case '보수완료':
      return { review_status: 'REVIEWED', repair_status: 'COMPLETED' };
    case '보수 불필요':
      return { review_status: 'REVIEWED', repair_status: 'NOT_NEEDED' };
    case '검토 완료':
      return { review_status: 'REVIEWED', repair_status: 'BEFORE' };
    case '검토 필요':
    case '미처리':
    default:
      return { review_status: 'PENDING', repair_status: 'BEFORE' };
  }
}

function formatSeoulDateTime(value: string | null): { date: string; time: string } {
  const parsed = value ? new Date(value) : new Date(Number.NaN);
  if (Number.isNaN(parsed.getTime())) return { date: '', time: '' };

  return {
    date: parsed.toLocaleDateString('sv-SE', { timeZone: 'Asia/Seoul' }),
    time: parsed.toLocaleTimeString('ko-KR', {
      timeZone: 'Asia/Seoul',
      hour12: false,
    }),
  };
}

export function apiDefectToInspectionItem(
  defect: ApiDefect,
  reportId = String(defect.session_id),
): InspectionItem {
  const displayedAt = formatSeoulDateTime(defect.detected_at);
  const snapshotUrl = defect.snapshot_path?.trim()
    ? resolveMediaUrl(defect.snapshot_path)
    : PLACEHOLDER_IMAGE;

  return {
    id: String(defect.id),
    reportId,
    defectType: getDefectTypeLabel(defect.defect_type),
    severity: normalizeSeverity(defect.severity),
    date: displayedAt.date,
    time: displayedAt.time,
    location: defect.approx_position_m == null
      ? '위치 정보 없음'
      : `출발점 기준 ${defect.approx_position_m}m`,
    imageUrl: snapshotUrl,
    confidence: defect.confidence <= 1
      ? defect.confidence * 100
      : defect.confidence,
    recommendation: '추가 검토 필요',
    aiModel: 'Unknown',
    status: defectStatusToUi(defect.review_status, defect.repair_status),
    actionType: defect.action_type ?? '추가 정밀 관측',
    timeline: [],
  };
}

function formatDuration(start: string | null, end: string | null): string {
  if (!start || !end) return '00:00:00';
  const milliseconds = new Date(end).getTime() - new Date(start).getTime();
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return '00:00:00';
  const totalSeconds = Math.floor(milliseconds / 1000);
  return [
    Math.floor(totalSeconds / 3600),
    Math.floor((totalSeconds % 3600) / 60),
    totalSeconds % 60,
  ].map((part) => String(part).padStart(2, '0')).join(':');
}

export function apiSessionToReportMeta(
  session: ApiSession,
  robotName?: string,
  operatorName?: string,
): ReportMeta {
  const total = Number(session.defect_summary?.total ?? 0);
  const critical = Number(session.defect_summary?.critical ?? 0);
  const healthScore = Math.max(0, Math.min(100, 100 - critical * 8 - (total - critical) * 2));

  return {
    id: String(session.id),
    region: `점검 구역 ${session.district_id}`,
    robotName: robotName ?? `로봇 ${session.robot_id}`,
    operatorName: operatorName ?? (
      session.approved_by === null ? '정보 없음' : `운영자 ${session.approved_by}`
    ),
    date: formatSeoulDateTime(session.started_at).date,
    duration: formatDuration(session.started_at, session.ended_at),
    totalInspected: total,
    criticalCount: critical,
    healthScore,
    gradeZone: healthScore >= 90 ? 'A 구역' : healthScore >= 80 ? 'B 구역' : 'C 구역',
  };
}

export function apiReportToReportMeta(
  report: ApiReport,
  session: ApiSession | undefined,
  defects: ApiDefect[],
  logs: ApiDriveLog[],
  robotName?: string,
  operatorName?: string,
): ReportMeta {
  const criticalCount = defects.filter(
    (defect) => normalizeSeverity(defect.severity) === 'critical',
  ).length;
  const healthScore = Math.max(
    0,
    Math.min(100, 100 - criticalCount * 8 - (defects.length - criticalCount) * 2),
  );
  const generatedAt = formatSeoulDateTime(report.generated_at);
  const totalDefects = report.total_defect_count ?? defects.length;
  const reportCriticalCount = report.critical_defect_count ?? criticalCount;
  const reportHealthScore = Math.max(
    0,
    Math.min(100, 100 - reportCriticalCount * 8 - (totalDefects - reportCriticalCount) * 2),
  );

  return {
    id: String(report.id),
    region: report.region || (session ? `점검 구역 ${session.district_id}` : `세션 ${report.session_id}`),
    robotName: report.robot_name && report.robot_model
      ? `${report.robot_name} (${report.robot_model})`
      : robotName ?? (
      session ? `로봇 ${session.robot_id}` : '정보 없음'
    ),
    operatorName: report.operator_name && report.operator_role
      ? `${report.operator_name} (${report.operator_role})`
      : operatorName ?? (
      session?.approved_by == null ? '정보 없음' : `운영자 ${session.approved_by}`
    ),
    date: report.inspection_date || generatedAt.date,
    duration: report.duration || (
      session ? formatDuration(session.started_at, session.ended_at) : '00:00:00'
    ),
    totalInspected: totalDefects,
    criticalCount: reportCriticalCount,
    healthScore: reportHealthScore,
    gradeZone: reportHealthScore >= 90 ? 'A 구역' : reportHealthScore >= 80 ? 'B 구역' : 'C 구역',
    inspectionLogs: logs.map(apiDriveLogToUiLog).reverse(),
  };
}

export function apiDriveLogToUiLog(log: ApiDriveLog): InspectionLog {
  const displayedAt = formatSeoulDateTime(log.logged_at);
  return {
    time: displayedAt.time,
    text: log.note ?? log.command ?? log.agent_state ?? '주행 상태 기록',
    type: 'info',
  };
}

// TODO: 결함별 전·포착·후 타임라인 저장 구조가 확정되면 timeline 변환을 연결한다.
