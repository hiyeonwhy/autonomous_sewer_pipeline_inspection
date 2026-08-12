import assert from 'node:assert/strict'
import test from 'node:test'

import {
  agentApiBaseUrl,
  agentWebSocketUrl,
  eventToInspectionLog,
  explicitRunningInspectionIdentity,
  inspectionIdentityKey,
  missionUiState,
  postMissionAction,
  progressAnnouncementUiState,
  readAgentLogs,
  readDrive,
  readModels,
  readMission,
  readNavigation,
  readProgressSummary,
  readYoloStatus,
  reportProgressDefect,
  setNavigationVersion,
  shouldApplyProgressAnnouncement,
  shouldApplyProgressRestSnapshot,
  driveCommandUiState,
  motorOutputPercent,
  shouldAcceptAutonomousCompletion,
  obstaclePerceptionUiState,
  ultrasonicTelemetryUiState,
  yoloStatusUiState,
  yoloStreamUrl,
} from '../src/services/agent.mjs'

test('stale completion is rejected until the current inspection has reported running', () => {
  const currentIdentity = inspectionIdentityKey(242, 'operation-new')
  assert.equal(shouldAcceptAutonomousCompletion({
    currentIdentity,
    runningObservedIdentity: null,
    missionState: 'COMPLETED',
  }), false)
  assert.equal(shouldAcceptAutonomousCompletion({
    currentIdentity,
    runningObservedIdentity: inspectionIdentityKey(241, 'operation-old'),
    missionState: 'COMPLETED',
  }), false)
})

test('missing telemetry state never counts as an explicit running observation', () => {
  assert.equal(explicitRunningInspectionIdentity({
    expectedSessionId: 242,
    expectedOperationId: 'operation-new',
    telemetrySessionId: 242,
    telemetryOperationId: 'operation-new',
  }), null)
})

test('only matching raw Agent running telemetry produces persisted completion evidence', () => {
  const currentIdentity = inspectionIdentityKey(242, 'operation-new')
  assert.equal(explicitRunningInspectionIdentity({
    expectedSessionId: 242,
    expectedOperationId: 'operation-new',
    telemetrySessionId: 241,
    telemetryOperationId: 'operation-old',
    hermesState: 'RUNNING',
  }), null)
  assert.equal(explicitRunningInspectionIdentity({
    expectedSessionId: 242,
    expectedOperationId: 'operation-new',
    telemetrySessionId: 242,
    telemetryOperationId: 'operation-new',
    missionState: 'RUNNING',
  }), currentIdentity)
})

test('persisted running identity accepts completion after a reload', () => {
  const persistedRunningIdentity = inspectionIdentityKey(242, 'operation-new')
  assert.equal(shouldAcceptAutonomousCompletion({
    currentIdentity: inspectionIdentityKey(242, 'operation-new'),
    runningObservedIdentity: persistedRunningIdentity,
    missionState: 'RUNNING',
  }), false)
  assert.equal(shouldAcceptAutonomousCompletion({
    currentIdentity: inspectionIdentityKey(242, 'operation-new'),
    runningObservedIdentity: persistedRunningIdentity,
    missionState: 'COMPLETED',
  }), true)
})

test('progress summary helper reads the canonical Qwen announcement endpoint', async () => {
  const calls = []
  const payload = {
    eventType: 'OBSTACLE_DETECTED',
    message: '전방 장애물을 인식해 우측 회피를 시작합니다.',
    status: 'ready',
  }
  const result = await readProgressSummary(async (url, options) => {
    calls.push({ url, options })
    return { ok: true, json: async () => payload }
  }, { VITE_AGENT_API_URL: 'http://agent.test:8000/' })

  assert.deepEqual(result, payload)
  assert.deepEqual(calls, [{
    url: 'http://agent.test:8000/api/progress-summary',
    options: { cache: 'no-store' },
  }])
})

test('progress summary UI accepts only the four Qwen event types', () => {
  assert.deepEqual(progressAnnouncementUiState({ status: 'pending' }), {
    message: '상황 분석 중',
    tone: 'pending',
  })
  assert.deepEqual(progressAnnouncementUiState({
    status: 'ready',
    eventType: 'OBSTACLE_DETECTED',
    message: '장애물 인식, 회피 중',
  }), {
    message: '장애물 인식, 회피 중',
    tone: 'ready',
  })
  assert.deepEqual(progressAnnouncementUiState({
    status: 'ready',
    eventType: 'CAMERA_ERROR',
    message: '카메라 오류입니다.',
  }), {
    message: '안내 이벤트 대기 중',
    tone: 'idle',
  })
  assert.deepEqual(progressAnnouncementUiState({ status: 'error' }), {
    message: '',
    tone: 'idle',
  })
})

test('dashboard speed uses the strongest signed motor output as percent', () => {
  assert.equal(motorOutputPercent({ left: 0.65, right: -0.40 }), 65)
  assert.equal(motorOutputPercent({ left: 0, right: 0 }), 0)
  assert.equal(motorOutputPercent(null), 0)
})

test('REST progress snapshot never overwrites a WebSocket progress event', () => {
  assert.equal(shouldApplyProgressRestSnapshot(false), true)
  assert.equal(shouldApplyProgressRestSnapshot(true), false)
})

test('progress revision rejects queued events older than the initial snapshot', () => {
  assert.equal(shouldApplyProgressAnnouncement(8, { revision: 7 }), false)
  assert.equal(shouldApplyProgressAnnouncement(8, { revision: 8 }), true)
  assert.equal(shouldApplyProgressAnnouncement(8, { revision: 9 }), true)
  assert.equal(shouldApplyProgressAnnouncement(8, { revision: 10, status: 'error' }), false)
  assert.equal(shouldApplyProgressAnnouncement(8, { revision: 10, status: 'unavailable' }), false)
})

test('confirmed defect is reported to the local Qwen progress endpoint', async () => {
  const calls = []
  const payload = {
    eventId: 'defect:defect-1',
    missionId: 'mission-test123',
    defectType: 'CRACK',
    confidence: 0.91,
    locationM: 2.4,
  }
  const result = await reportProgressDefect(async (url, options) => {
    calls.push({ url, options })
    return { ok: true, json: async () => ({ accepted: true }) }
  }, payload, { VITE_AGENT_API_URL: 'http://agent.test:8000/' })

  assert.deepEqual(result, { accepted: true })
  assert.deepEqual(calls, [{
    url: 'http://agent.test:8000/api/progress-summary/defect',
    options: {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  }])
})

test('navigation version helpers read and update the canonical AI endpoint', async () => {
  const calls = []
  const fetchImpl = async (url, options = {}) => {
    calls.push({ url, options })
    return {
      ok: true,
      json: async () => ({ activeVersion: options.method === 'PUT' ? 'v2' : 'v1' }),
    }
  }
  const env = { VITE_AGENT_API_URL: 'http://agent.test:8000/' }

  assert.equal((await readNavigation(fetchImpl, env)).activeVersion, 'v1')
  assert.equal((await setNavigationVersion(fetchImpl, 'v2', env)).activeVersion, 'v2')
  assert.deepEqual(calls, [
    {
      url: 'http://agent.test:8000/api/navigation',
      options: { cache: 'no-store' },
    },
    {
      url: 'http://agent.test:8000/api/navigation',
      options: {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version: 'v2' }),
      },
    },
  ])
})

test('agent endpoints use configured server and derive websocket URL', () => {
  const env = { VITE_AGENT_API_URL: 'http://10.0.0.5:8000' }
  assert.equal(agentApiBaseUrl(env), 'http://10.0.0.5:8000')
  assert.equal(agentWebSocketUrl(env), 'ws://10.0.0.5:8000/ws/events')
})

test('YOLO stream URL prefers its explicit override', () => {
  assert.equal(yoloStreamUrl({
    VITE_AGENT_API_URL: 'http://agent.test:8000',
    VITE_YOLO_STREAM_URL: 'http://video.test/annotated.mjpg',
  }), 'http://video.test/annotated.mjpg')
})

test('YOLO stream URL derives from the normalized agent API URL by default', () => {
  assert.equal(
    yoloStreamUrl({ VITE_AGENT_API_URL: 'http://agent.test:8000/' }),
    'http://agent.test:8000/api/vision/yolo-stream.mjpg',
  )
  assert.equal(yoloStreamUrl({}), 'http://127.0.0.1:8000/api/vision/yolo-stream.mjpg')
})

test('YOLO status reads the canonical vision endpoint without caching', async () => {
  const calls = []
  const payload = { model: 'yolo11n', detections: 3, latencyMs: 27.4, error: null }
  const result = await readYoloStatus(async (url, options) => {
    calls.push({ url, options })
    return { ok: true, json: async () => payload }
  }, { VITE_AGENT_API_URL: 'http://agent.test:8000/' })

  assert.deepEqual(result, payload)
  assert.deepEqual(calls, [{
    url: 'http://agent.test:8000/api/vision/yolo',
    options: { cache: 'no-store' },
  }])
})

test('YOLO UI labels only values supplied by the status API', () => {
  assert.deepEqual(yoloStatusUiState(), {
    modelLabel: '준비 중',
    detectionsLabel: '미연동',
    latencyLabel: '미연동',
    errorLabel: '미연동',
  })
  assert.deepEqual(yoloStatusUiState({
    model: 'yolo11n', detections: 0, latencyMs: 27.4, error: null,
  }), {
    modelLabel: 'yolo11n',
    detectionsLabel: '0건',
    latencyLabel: '27ms',
    errorLabel: '없음',
  })
  assert.deepEqual(yoloStatusUiState({
    model: 'models/yolo11n.pt',
    error: null,
    latestResult: { detections: [], latencyMs: 14.2 },
  }), {
    modelLabel: 'models/yolo11n.pt',
    detectionsLabel: '0건',
    latencyLabel: '14ms',
    errorLabel: '없음',
  })
})

test('completed mission opens a truthful completion modal without fake metrics', () => {
  const state = missionUiState({
    state: 'COMPLETED',
    distanceM: null,
    reportAvailable: false,
    message: '실시간 탐사 임무가 안전하게 종료됐습니다.',
  })

  assert.equal(state.status, '완료')
  assert.equal(state.showResult, true)
  assert.equal(state.distanceLabel, '미연동')
  assert.equal(state.resultTone, 'success')
})

test('blocked mission opens a failure modal and never reports safe completion', () => {
  const state = missionUiState({
    state: 'FAILED_BLOCKED',
    distanceM: null,
    reportAvailable: false,
    message: '장애물 우회에 실패하여 자율 임무를 종료했습니다.',
  })

  assert.equal(state.status, '막힘 종료')
  assert.equal(state.showResult, true)
  assert.equal(state.resultTone, 'danger')
  assert.match(state.resultTitle, /주행 불가/)
})

test('system error termination opens the result modal and allows recovery', () => {
  const state = missionUiState({
    state: 'ERROR',
    distanceM: null,
    reportAvailable: false,
    message: 'Edge 안전 점검 실패로 임무를 종료했습니다.',
  })

  assert.equal(state.status, '오류 종료')
  assert.equal(state.showResult, true)
  assert.equal(state.resultTone, 'danger')
  assert.match(state.resultTitle, /임무 종료/)
})

test('emergency stop opens the result modal', () => {
  const state = missionUiState({
    state: 'EMERGENCY_STOP',
    distanceM: null,
    message: '긴급 정지로 임무를 종료했습니다.',
  })

  assert.equal(state.status, '긴급 정지')
  assert.equal(state.showResult, true)
  assert.equal(state.resultTone, 'danger')
})

test('agent event becomes a dashboard log with its category preserved', () => {
  const log = eventToInspectionLog({
    timestamp: '2026-07-23T03:00:01Z',
    category: 'DECISION',
    level: 'alert',
    message: '좌우 회피 공간이 부족해 STOP을 선택했습니다.',
  })

  assert.deepEqual(log, {
    time: '03:00:01',
    text: '[DECISION] 좌우 회피 공간이 부족해 STOP을 선택했습니다.',
    type: 'alert',
  })
})

test('mission API helpers keep the server response as the canonical state', async () => {
  const calls = []
  const fetchImpl = async (url, options = {}) => {
    calls.push({ url, options })
    return {
      ok: true,
      json: async () => ({ state: 'RUNNING', distanceM: null }),
    }
  }
  const env = { VITE_AGENT_API_URL: 'http://agent.test' }

  const read = await readMission(fetchImpl, env)
  const started = await postMissionAction(fetchImpl, 'start', { area: '시험 관로' }, env)

  assert.equal(read.state, 'RUNNING')
  assert.equal(started.distanceM, null)
  assert.equal(calls[0].url, 'http://agent.test/api/mission')
  assert.equal(calls[1].url, 'http://agent.test/api/mission/start')
  assert.equal(calls[1].options.method, 'POST')
})

test('agent log history is loaded from the server after a dashboard reload', async () => {
  const events = [{ id: 'evt-1', category: 'SAFETY', level: 'critical', message: 'STOP 유지', timestamp: '2026-07-23T03:00:01Z' }]
  const fetchImpl = async (url) => ({
    ok: true,
    json: async () => {
      assert.equal(url, 'http://agent.local:8000/api/logs')
      return events
    },
  })

  assert.deepEqual(
    await readAgentLogs(fetchImpl, { VITE_AGENT_API_URL: 'http://agent.local:8000' }),
    events,
  )
})

test('drive status exposes model and simulated wheel command without fake metric speed', async () => {
  const payload = {
    controlMode: 'simulation-no-motor',
    perceptionModel: 'segformer-b2',
    decisionModel: 'qwen2.5-vl-3b-instruct-awq',
    latestCommand: {
      action: 'FOLLOW_PIPE',
      leftSpeed: 0.37,
      rightSpeed: 0.13,
      safetyApproved: true,
      reason: 'P/PID 차동 조향',
    },
  }
  const fetchImpl = async (url) => ({
    ok: true,
    json: async () => {
      assert.equal(url, 'http://agent.local:8000/api/drive')
      return payload
    },
  })

  const drive = await readDrive(fetchImpl, { VITE_AGENT_API_URL: 'http://agent.local:8000' })
  const ui = driveCommandUiState(drive.latestCommand)

  assert.equal(drive.perceptionModel, 'segformer-b2')
  assert.equal(ui.steeringLabel, '우측 조향')
  assert.equal(ui.wheelLabel, 'L 37% · R 13%')
  assert.equal(ui.metricSpeed, null)
})

test('real motor command is clearly labeled with bounded wheel output', () => {
  const ui = driveCommandUiState({
    action: 'FOLLOW_PIPE',
    leftSpeed: 0.50,
    rightSpeed: 0.50,
    safetyApproved: true,
    controlMode: 'real-motor',
  })

  assert.equal(ui.wheelLabel, 'L 50% · R 50%')
  assert.equal(ui.safetyLabel, '통과 · 실제 모터')
})

test('model status helper reads downloaded and loaded state from FastAPI', async () => {
  const payload = {
    perception: { name: 'segformer-b2', downloaded: true, loaded: false },
    decision: { name: 'qwen2.5-vl-3b-instruct-awq', downloaded: true, loaded: false },
  }
  const fetchImpl = async (url) => ({
    ok: true,
    json: async () => {
      assert.equal(url, 'http://agent.local:8000/api/models')
      return payload
    },
  })

  assert.deepEqual(
    await readModels(fetchImpl, { VITE_AGENT_API_URL: 'http://agent.local:8000' }),
    payload,
  )
})

test('ultrasonic telemetry never labels close cylindrical-pipe echoes as a hard stop', () => {
  assert.deepEqual(ultrasonicTelemetryUiState({ ultrasonicDistanceM: null }), {
    distanceLabel: '무반사 · 열린 구간 가능',
    stateLabel: '주행 허용',
    tone: 'info',
  })
  assert.deepEqual(ultrasonicTelemetryUiState({ ultrasonicDistanceM: 0.40 }), {
    distanceLabel: '40.0 cm',
    stateLabel: '45cm 속도 보조 구간',
    tone: 'warn',
  })
  assert.deepEqual(ultrasonicTelemetryUiState({ ultrasonicDistanceM: 0.04 }), {
    distanceLabel: '4.0 cm',
    stateLabel: '근거리 반사 · 정지 조건 아님',
    tone: 'info',
  })
})

test('obstacle perception exposes blocked probability and directional free space', () => {
  assert.deepEqual(obstaclePerceptionUiState({
    pathBlockedProbability: 0.72,
    leftFreeScore: 0.81,
    centerFreeScore: 0.28,
    rightFreeScore: 0.35,
    confidence: 0.85,
    latencyMs: 74.2,
  }), {
    stateLabel: '장애물 감지',
    tone: 'warn',
    blockedLabel: '72%',
    freeSpaceLabel: '좌 81% · 중앙 28% · 우 35%',
    confidenceLabel: '85%',
    latencyLabel: '74ms',
  })
})
