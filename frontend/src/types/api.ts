export interface ApiDefectSummary {
  total?: number;
  critical?: number;
  warning?: number;
  normal?: number;
  [key: string]: unknown;
}

export interface ApiSession {
  id: number;
  district_id: number;
  priority_score_id: number | null;
  robot_id: number;
  approved_by: number | null;
  mode: string;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  defect_summary: ApiDefectSummary | null;
}

export interface CreateSessionRequest {
  district_id: number;
  priority_score_id: number | null;
  robot_id: number;
  approved_by: number | null;
  mode: 'AUTO' | 'MANUAL';
}

export interface ApiSurveyRun {
  /** 점검 위치 — 동·주소. 보고서 「점검 위치」 칸에 실린다. */
  survey_location?: string | null;
  /** 점검 구간 — 동 안의 구간. 보고서 「점검 구간」 칸에 실린다. */
  inspection_section?: string | null;
  total_distance_m?: number | null;
  undriven_distance_m?: number | null;
}

export interface UpdateSessionRequest {
  status?: string | null;
  ended_at?: string | null;
  defect_summary?: ApiDefectSummary | null;
  survey?: ApiSurveyRun | null;
}

export interface ApiDefect {
  id: number;
  session_id: number;
  defect_type: string;
  severity: string;
  confidence: number;
  approx_position_m: number | null;
  snapshot_path: string;
  repair_status: 'BEFORE' | 'IN_PROGRESS' | 'COMPLETED' | 'NOT_NEEDED';
  action_type: string | null;
  review_status: 'PENDING' | 'REVIEWED';
  detected_at: string;
}

export interface DefectReviewUpdate {
  review_status?: 'PENDING' | 'REVIEWED';
  repair_status?: 'BEFORE' | 'IN_PROGRESS' | 'COMPLETED' | 'NOT_NEEDED';
  action_type?: string | null;
}

export interface DefectCaptureCreate {
  defect_type: string;
  severity: string;
  confidence: number;
  approx_position_m: number | null;
  snapshot_path: string;
  detected_at?: string | null;
}

export interface ApiImuReading {
  roll?: number;
  pitch?: number;
  yaw?: number;
  angular_velocity?: {
    x?: number;
    y?: number;
    z?: number;
  };
  linear_acceleration?: {
    x?: number;
    y?: number;
    z?: number;
  };
  [key: string]: unknown;
}

export interface ApiDriveLog {
  id: number;
  session_id: number;
  ultrasonic_distance: number | null;
  imu_reading: ApiImuReading | null;
  command: string | null;
  command_source: string | null;
  agent_state: string | null;
  note: string | null;
  logged_at: string;
}

export interface DriveLogCreate {
  ultrasonic_distance?: number | null;
  imu_reading?: Record<string, unknown> | null;
  command?: string | null;
  command_source?: string | null;
  agent_state?: string | null;
  note?: string | null;
  logged_at?: string | null;
}

export interface ApiRobot {
  id: number;
  name: string;
  model: string;
  status: string;
}

export interface ApiRobotStatus {
  id?: number;
  name?: string;
  model?: string;
  status?: string;
  connected?: boolean;
  control_mode?: string;
  active_session_id?: number | null;
  communication_latency_ms?: number;
  speed_mps?: number;
  distance_m?: number;
}

export interface ApiOperator {
  id: number;
  name: string;
  role: string;
}

export interface ApiReport {
  id: number;
  session_id: number;
  region: string;
  robot_name: string;
  robot_model: string;
  operator_name: string;
  operator_role: string;
  inspection_date: string;
  duration: string;
  total_defect_count: number;
  critical_defect_count: number;
  summary: string;
  generated_at: string;
}

export interface ApiReconstruction {
  id: number;
  session_id: number;
  status: string;
  stage: string;
  progress_percent: number;
  image_count: number;
  model_path: string | null;
  thumbnail_path: string | null;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
}

export interface ReconstructionCreate {
  image_count: number;
}

export interface ReconstructionUpdate {
  status: string;
  stage: string;
  progress_percent: number;
  model_path?: string | null;
  thumbnail_path?: string | null;
  error_message?: string | null;
}

export interface ApiHealth {
  status?: string;
  server?: string;
  mongodb?: string;
  inference_server?: string;
  robot?: string;
}

export interface ApiPageResponse<T> {
  content?: T[];
  items?: T[];
  results?: T[];
  page?: number;
  size?: number;
  total_elements?: number;
  total_pages?: number;
}
