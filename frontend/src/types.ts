export type AppTab = 'realtime' | 'report' | 'priority';

export type DefectSeverity = 'critical' | 'warning' | 'normal';

export const ACTION_TYPES = ['비굴착 CIPP 보수', '고압 세정/준설', '추가 정밀 관측', '기타 (일반 관찰)'] as const;
export const STATUS_OPTIONS = ['검토 필요', '검토 완료', '작업 예정', '보수 완료'] as const;

export interface TimelineEvent {
  id: string;
  time: string;
  title: string;
  desc: string;
  distance: number; // in meters
  iconType: 'start' | 'hazard' | 'capture' | 'normal';
}

export interface InspectionItem {
  id: string;
  defectType: string;
  severity: DefectSeverity;
  date: string;
  time: string;
  location: string;
  imageUrl: string;
  timeline: TimelineEvent[];
  reportId: string;
  recommendation: string;
  confidence: number; // e.g. 94.8
  aiModel: string;
  status?: string; // '검토 필요' | '미처리' | '담당자 배정'
  actionType?: string; // e.g. '비굴착 CIPP 보수'
  coordinates?: string;
  camId?: string;
  originalImageId?: string;
}

export interface ReportMeta {
  id: string;
  region: string;
  robotName?: string;
  operatorName?: string;
  date: string;
  duration: string;
  totalInspected: number;
  criticalCount: number;
  healthScore: number;
  gradeZone: string;
  inspectionLogs?: InspectionLog[];
}

export interface InspectionLog {
  time: string;
  text: string;
  type: 'info' | 'warn' | 'success' | 'alert';
}
