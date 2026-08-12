const DEFAULT_AGENT_API_URL = 'http://127.0.0.1:8000'

export function agentApiBaseUrl(env = {}) {
  return (env.VITE_AGENT_API_URL || DEFAULT_AGENT_API_URL).replace(/\/$/, '')
}

export function agentWebSocketUrl(env = {}) {
  const configured = env.VITE_AGENT_WS_URL
  if (configured) return configured
  return `${agentApiBaseUrl(env).replace(/^http/, 'ws')}/ws/events`
}

export function yoloStreamUrl(env = {}) {
  return env.VITE_YOLO_STREAM_URL || `${agentApiBaseUrl(env)}/api/vision/yolo-stream.mjpg`
}

export async function readYoloStatus(fetchImpl = fetch, env = {}) {
  const response = await fetchImpl(`${agentApiBaseUrl(env)}/api/vision/yolo`, {
    cache: 'no-store',
  })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.detail || payload.error || 'YOLO 상태 조회에 실패했습니다.')
  return payload
}

export function yoloStatusUiState(status = null) {
  const model = status?.model ?? status?.modelName
  const latest = status?.latestResult ?? status?.latest_result
  const detections = status?.detections
    ?? status?.detectionCount
    ?? (Array.isArray(latest?.detections) ? latest.detections.length : undefined)
  const latencyMs = status?.latencyMs ?? status?.latency_ms ?? latest?.latencyMs ?? latest?.latency_ms
  const hasErrorField = status !== null && Object.prototype.hasOwnProperty.call(status, 'error')

  return {
    modelLabel: typeof model === 'string' && model ? model : '준비 중',
    detectionsLabel: Number.isFinite(detections) ? `${detections}건` : '미연동',
    latencyLabel: Number.isFinite(latencyMs) ? `${Math.round(latencyMs)}ms` : '미연동',
    errorLabel: typeof status?.error === 'string' && status.error
      ? status.error
      : hasErrorField ? '없음' : '미연동',
  }
}

export async function readMission(fetchImpl = fetch, env = {}) {
  const response = await fetchImpl(`${agentApiBaseUrl(env)}/api/mission`, {
    cache: 'no-store',
  })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.detail || 'AI Agent 상태 조회에 실패했습니다.')
  return payload
}

export async function readAgentLogs(fetchImpl = fetch, env = {}) {
  const response = await fetchImpl(`${agentApiBaseUrl(env)}/api/logs`, {
    cache: 'no-store',
  })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.detail || 'AI Agent 로그 조회에 실패했습니다.')
  return payload
}

export async function readProgressSummary(fetchImpl = fetch, env = {}) {
  const response = await fetchImpl(`${agentApiBaseUrl(env)}/api/progress-summary`, {
    cache: 'no-store',
  })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.detail || 'Qwen 진행 상황 요약 조회에 실패했습니다.')
  return payload
}

export function shouldApplyProgressRestSnapshot(webSocketHasProgress) {
  return webSocketHasProgress !== true
}

export function shouldApplyProgressAnnouncement(currentRevision, announcement) {
  if (announcement?.status === 'error' || announcement?.status === 'unavailable') {
    return false
  }
  const incomingRevision = announcement?.revision
  if (!Number.isInteger(incomingRevision) || incomingRevision < 0) {
    return currentRevision < 0
  }
  return incomingRevision >= currentRevision
}

export async function reportProgressDefect(fetchImpl = fetch, defect, env = {}) {
  const response = await fetchImpl(`${agentApiBaseUrl(env)}/api/progress-summary/defect`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(defect),
  })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.detail || 'Qwen 결함 안내 요청에 실패했습니다.')
  return payload
}

export function progressAnnouncementUiState(announcement = null) {
  const allowedEvents = new Set([
    'ROBOT_ACTION',
    'DEFECT_DETECTED',
    'OBSTACLE_DETECTED',
    'OBSTACLE_AVOIDANCE_COMPLETED',
    'EXPLORATION_UNAVAILABLE',
  ])
  if (announcement?.status === 'pending') {
    return { message: '상황 분석 중', tone: 'pending' }
  }
  if (announcement?.status === 'ready') {
    if (
      (allowedEvents.has(announcement.eventType)
        || announcement.eventType === null
        || announcement.eventType === undefined)
      && typeof announcement.message === 'string'
      && announcement.message.trim()
    ) {
      const oneLine = announcement.message.trim().split(/\r?\n/, 1)[0].slice(0, 20)
      return { message: oneLine, tone: 'ready' }
    }
  }
  if (announcement?.status === 'error' || announcement?.status === 'unavailable') {
    return { message: '', tone: 'idle' }
  }
  return { message: '안내 이벤트 대기 중', tone: 'idle' }
}

export function motorOutputPercent(output = null) {
  const left = Number.isFinite(output?.left) ? Math.abs(output.left) : 0
  const right = Number.isFinite(output?.right) ? Math.abs(output.right) : 0
  return Math.min(100, Math.max(0, Math.max(left, right) * 100))
}

export function inspectionIdentityKey(sessionId, operationId) {
  if (sessionId === undefined || sessionId === null || typeof operationId !== 'string' || !operationId) {
    return null
  }
  return JSON.stringify([String(sessionId), operationId])
}

export function explicitRunningInspectionIdentity({
  expectedSessionId,
  expectedOperationId,
  telemetrySessionId,
  telemetryOperationId,
  hermesState,
  missionState,
} = {}) {
  const expectedIdentity = inspectionIdentityKey(expectedSessionId, expectedOperationId)
  const telemetryIdentity = inspectionIdentityKey(telemetrySessionId, telemetryOperationId)
  const explicitlyRunning = [hermesState, missionState]
    .some((state) => String(state ?? '').toUpperCase() === 'RUNNING')
  return expectedIdentity && telemetryIdentity === expectedIdentity && explicitlyRunning
    ? expectedIdentity
    : null
}

export function shouldAcceptAutonomousCompletion({
  currentIdentity,
  runningObservedIdentity,
  missionState,
} = {}) {
  return Boolean(
    currentIdentity
    && runningObservedIdentity === currentIdentity
    && String(missionState ?? '').toUpperCase() === 'COMPLETED'
  )
}

export async function readDrive(fetchImpl = fetch, env = {}) {
  const response = await fetchImpl(`${agentApiBaseUrl(env)}/api/drive`, {
    cache: 'no-store',
  })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.detail || '주행 상태 조회에 실패했습니다.')
  return payload
}

export async function readNavigation(fetchImpl = fetch, env = {}) {
  const response = await fetchImpl(`${agentApiBaseUrl(env)}/api/navigation`, {
    cache: 'no-store',
  })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.detail || '주행 버전 조회에 실패했습니다.')
  return payload
}

export async function setNavigationVersion(fetchImpl = fetch, version, env = {}) {
  const response = await fetchImpl(`${agentApiBaseUrl(env)}/api/navigation`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version }),
  })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.detail || '주행 버전 변경에 실패했습니다.')
  return payload
}

export async function readModels(fetchImpl = fetch, env = {}) {
  const response = await fetchImpl(`${agentApiBaseUrl(env)}/api/models`, {
    cache: 'no-store',
  })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.detail || 'AI 모델 상태 조회에 실패했습니다.')
  return payload
}

export function driveCommandUiState(command = null) {
  if (!command) {
    return {
      steeringLabel: '명령 대기',
      wheelLabel: 'L 0% · R 0%',
      metricSpeed: null,
      safetyLabel: '미연동',
    }
  }
  const left = Number.isFinite(command.leftSpeed) ? command.leftSpeed : 0
  const right = Number.isFinite(command.rightSpeed) ? command.rightSpeed : 0
  const steeringLabel = command.action === 'STOP'
    ? '정지'
    : left > right
      ? '우측 조향'
      : right > left
        ? '좌측 조향'
        : '중심 추종'
  return {
    steeringLabel,
    wheelLabel: `L ${Math.round(left * 100)}% · R ${Math.round(right * 100)}%`,
    metricSpeed: null,
    safetyLabel: command.safetyApproved
      ? command.controlMode === 'real-motor'
        ? '통과 · 실제 모터'
        : '통과 · Simulation'
      : '차단 · STOP',
  }
}

export async function postMissionAction(fetchImpl = fetch, action, body = {}, env = {}) {
  const response = await fetchImpl(`${agentApiBaseUrl(env)}/api/mission/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.detail || `AI Agent ${action} 요청에 실패했습니다.`)
  return payload
}

export function eventToInspectionLog(event) {
  const allowedTypes = new Set(['info', 'warn', 'success', 'alert'])
  return {
    time: typeof event.timestamp === 'string' ? event.timestamp.slice(11, 19) : '--:--:--',
    text: `[${event.category || 'SYSTEM'}] ${event.message || '내용 없음'}`,
    type: allowedTypes.has(event.level) ? event.level : 'info',
  }
}

export function ultrasonicTelemetryUiState(observation = null) {
  const distance = observation?.ultrasonicDistanceM
  if (distance === null) {
    return {
      distanceLabel: '무반사 · 열린 구간 가능',
      stateLabel: '주행 허용',
      tone: 'info',
    }
  }
  if (!Number.isFinite(distance)) {
    return {
      distanceLabel: '측정 대기',
      stateLabel: '센서 상태 확인 중',
      tone: 'neutral',
    }
  }
  const distanceLabel = `${(distance * 100).toFixed(1)} cm`
  if (distance <= 0.05) {
    return { distanceLabel, stateLabel: '근거리 반사 · 정지 조건 아님', tone: 'info' }
  }
  if (distance <= 0.45) {
    return { distanceLabel, stateLabel: '45cm 속도 보조 구간', tone: 'warn' }
  }
  return { distanceLabel, stateLabel: '전방 여유', tone: 'success' }
}

export function obstaclePerceptionUiState(perception = null) {
  const percent = (value) => Number.isFinite(value) ? `${Math.round(value * 100)}%` : '대기'
  const blocked = perception?.pathBlockedProbability
  const stateLabel = Number.isFinite(blocked)
    ? blocked >= 0.60 ? '장애물 감지' : '전방 경로 열림'
    : 'AI 인식 대기'
  return {
    stateLabel,
    tone: Number.isFinite(blocked) && blocked >= 0.60 ? 'warn' : Number.isFinite(blocked) ? 'success' : 'neutral',
    blockedLabel: percent(blocked),
    freeSpaceLabel: `좌 ${percent(perception?.leftFreeScore)} · 중앙 ${percent(perception?.centerFreeScore)} · 우 ${percent(perception?.rightFreeScore)}`,
    confidenceLabel: percent(perception?.confidence),
    latencyLabel: Number.isFinite(perception?.latencyMs) ? `${Math.round(perception.latencyMs)}ms` : '대기',
  }
}

export function missionUiState(mission = {}) {
  const distanceLabel = Number.isFinite(mission.distanceM)
    ? `${mission.distanceM.toFixed(2)} m`
    : '미연동'
  if (mission.state === 'COMPLETED') {
    return {
      status: '완료',
      showResult: true,
      resultTone: 'success',
      resultTitle: '실시간 점검 완료 보고',
      resultMessage: mission.message || '실시간 탐사 임무가 안전하게 종료됐습니다.',
      distanceLabel,
    }
  }
  if (mission.state === 'FAILED_BLOCKED') {
    return {
      status: '막힘 종료',
      showResult: true,
      resultTone: 'danger',
      resultTitle: '주행 불가 · 임무 종료',
      resultMessage: mission.message || '진행 경로가 막혀 자율 임무를 종료했습니다.',
      distanceLabel,
    }
  }

  if (mission.state === 'EMERGENCY_STOP') {
    return {
      status: '긴급 정지',
      showResult: true,
      resultTone: 'danger',
      resultTitle: '긴급 정지 · 임무 종료',
      resultMessage: mission.message || '긴급 정지로 탐사 임무를 종료했습니다.',
      distanceLabel,
    }
  }
  if (mission.state === 'ERROR') {
    return {
      status: '오류 종료',
      showResult: true,
      resultTone: 'danger',
      resultTitle: '시스템 오류 · 임무 종료',
      resultMessage: mission.message || '시스템 오류로 탐사 임무를 종료했습니다.',
      distanceLabel,
    }
  }

  const statusByState = {
    IDLE: '점검 전',
    PREPARING: '준비 중',
    RUNNING: '점검 중',
    STOPPING: '정지 중',
  }
  return {
    status: statusByState[mission.state] || '연결 확인 중',
    showResult: false,
    resultTone: 'neutral',
    resultTitle: '',
    resultMessage: mission.message || '',
    distanceLabel,
  }
}
