import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const root = new URL('../', import.meta.url)

async function source(path) {
  return readFile(new URL(path, root), 'utf8')
}

test('unimplemented features are visibly identified without a sample report label', async () => {
  const [app, header, monitor] = await Promise.all([
    source('src/App.tsx'),
    source('src/components/Header.tsx'),
    source('src/components/RealTimeMonitor.tsx'),
  ])

  // 예전에는 `useState<number>(0)` 이 있는지 봤습니다. UI 개편으로 그 선언이
  // 사라지자 깨졌는데, 정작 이 테스트의 의도(샘플 딱지 금지 + 미구현 표시)와는
  // 무관한 구현 세부였습니다. 의도를 그대로 보는 검사로 바꿉니다.
  assert.doesNotMatch(app, /\(샘플\)/)
  assert.match(header, />점검 보고서</)
  assert.doesNotMatch(header, /점검 보고서 \(샘플\)/)
  assert.match(monitor, /결함 추가 미연동/)
  assert.match(monitor, /시스템 종료 미연동/)
  assert.doesNotMatch(monitor, /보고서 생성 미연동/)
})

test('priority inspection start queues a Cloud command without changing the UI', async () => {
  const [app, priorityMap] = await Promise.all([
    source('src/App.tsx'),
    source('src/components/PriorityMap.tsx'),
  ])

  assert.match(app, /fetch\(`\$\{API_BASE_URL\}\/agent\/commands`/)
  assert.doesNotMatch(app, /fetch\(`\$\{AGENT_API_URL\}\/api\/hermes\/start`/)
  assert.match(priorityMap, /onStartInspection:[\s\S]{0,100}districtId\?: number/)
  assert.match(priorityMap, /scoreByCode\.get\(selectedDongData\.sgg_code\)\?\.district_id/)
  assert.match(app, /if \(!sectionName \|\| districtId === undefined\)/)
})

test('dashboard connects only pause, resume and finish while manual controls stay inert', async () => {
  const app = await source('src/App.tsx')
  const controls = app.slice(app.indexOf('function ControlPanel'), app.indexOf('function DonutChart'))

  assert.match(app, /sendRemoteHermesCommand\([\s\S]{0,80}'STOP'/)
  assert.doesNotMatch(app, /fetch\(`\$\{AGENT_API_URL\}\/api\/hermes\/stop`/)
  // 재개는 Agent 에 실제로 있는 start 로만 간다. Agent 에 pause/resume 엔드포인트가
  // 생기기 전까지 여기를 다른 경로로 바꾸면 버튼이 응답 없는 명령을 보내게 된다.
  assert.match(app, /const resumeHermesInspection[\s\S]{0,400}handleRobotAction\(\s*'running'/)
  // 「로봇 복귀」는 Agent 에 대응 명령이 없어 버튼째 제거했다. 되살리려면 먼저
  // Agent 에 복귀 엔드포인트가 있어야 한다.
  assert.doesNotMatch(controls, /onAction\('returning'\)/)
  assert.doesNotMatch(controls, /로봇 복귀/)
  assert.match(controls, /className="direction-up"[^>]*disabled/)
  assert.match(controls, /className="direction-left"[^>]*disabled/)
  assert.match(controls, /className="direction-right"[^>]*disabled/)
  assert.match(controls, /className="direction-down"[^>]*disabled/)
})

test('dashboard defects are isolated to the newly created session and deduplicated', async () => {
  const app = await source('src/App.tsx')

  assert.match(app, /sessionId\?: string \| number/)
  assert.match(app, /configuredInspection\?\.sessionId !== undefined[\s\S]{0,160}sessions\.find\(\(candidate\) => String\(candidate\.id\) === String\(configuredInspection\.sessionId\)\)[\s\S]{0,40}: sessions\[0\]/)
  assert.match(app, /configuredInspection\.sessionId === undefined \|\| configuredInspection\.operationId === undefined/)
  const submit = app.slice(app.indexOf('const submitInspectionSetup'), app.indexOf('// 고른 점검 위치'))
  assert.ok(submit.indexOf('setDefects([])') >= 0)
  assert.ok(submit.indexOf('setDefects([])') < submit.indexOf('handleRobotAction('))
  assert.match(app, /const seenDefectKeys = new Set<string>\(\)/)
  assert.match(app, /operation_id[\s\S]{0,120}event_id[\s\S]{0,120}detection_index/)
  assert.match(app, /JSON\.stringify\(\[String\(row\.operation_id\), String\(row\.event_id\), Number\(row\.detection_index\)\]\)/)
})

test('inspection command is unauthenticated same-origin and waits for laptop Agent ACK', async () => {
  const app = await source('src/App.tsx')
  const command = app.slice(app.indexOf('const sendRemoteHermesCommand'), app.indexOf('const handleRobotAction'))

  assert.match(command, /fetch\(`\$\{API_BASE_URL\}\/agent\/commands`/)
  assert.match(command, /command_type: commandType/)
  assert.match(command, /request_id: crypto\.randomUUID\(\)/)
  assert.match(command, /expires_in_s: 30/)
  assert.match(command, /statusPayload\?\.status === 'SUCCEEDED'/)
  assert.doesNotMatch(command, /Operator-Control-Key|prompt\(|AGENT_API_URL|\/api\/hermes\/start/)
})

test('a definitive Agent START rejection aborts the Cloud session without rollback STOP', async () => {
  const app = await source('src/App.tsx')
  const command = app.slice(app.indexOf('const sendRemoteHermesCommand'), app.indexOf('const handleRobotAction'))
  const submit = app.slice(app.indexOf('const submitInspectionSetup'), app.indexOf('// 고른 점검 위치'))

  assert.match(command, /throw new RemoteCommandRejectedError\(/)
  assert.match(submit, /startWasDefinitivelyRejected = error instanceof RemoteCommandRejectedError/)
  assert.match(submit, /if \(agentStartAttempted && !startWasDefinitivelyRejected && createdSessionId !== undefined\)/)
  assert.match(submit, /createdSessionId,[\s\S]{0,80}operationId,[\s\S]{0,40}true,/)
  assert.match(submit, /body: JSON\.stringify\(\{ status: 'ABORTED' \}\)/)
  assert.match(submit, /catch \(abortError\)[\s\S]{0,180}setConfiguredInspection\(null\);[\s\S]{0,80}setSession\(null\);/)
  const abortFailure = submit.slice(submit.indexOf('if (!abortResponse.ok)'), submit.indexOf('setConfiguredInspection(null);', submit.indexOf('if (!abortResponse.ok)')) + 30)
  assert.doesNotMatch(abortFailure, /setPage\('dashboard'\)|setConfiguredInspection\(\{/)
})

test('robot logs use AIagent and hide internal replay names', async () => {
  const app = await source('src/App.tsx')
  assert.match(app, /const sanitizeRobotLogText/)
  assert.match(app, /replaceAll\('Discord Hermes VL', 'AIagent'\)[\s\S]{0,80}replaceAll\('Hermes VL', 'AIagent'\)/)
  assert.match(app, /replaceAll\('기록1', ''\)/)
  assert.match(app, /category: sanitizeRobotLogSource/)
  assert.match(app, /message: sanitizeRobotLogText/)
  assert.match(app, /setServerEvents\(mapDriveLogRows\(driveLogs\)\)/)
})

test('dashboard camera and status continuously read the deployed EC2 Agent telemetry', async () => {
  const app = await source('src/App.tsx')

  assert.match(app, /agent\/yolo-frame\.jpg/)
  assert.match(app, /new WebSocket\(frameWebSocketUrl\)/)
  assert.match(app, /new URL\('\/ws\/agent\/frame-stream'/)
  assert.match(app, /currentSocket\.binaryType = 'arraybuffer'/)
  assert.match(app, /URL\.createObjectURL/)
  assert.match(app, /URL\.revokeObjectURL/)
  const cameraFeed = app.slice(app.indexOf('function CameraFeed'), app.indexOf('function DetectionSummary'))
  assert.match(app, /currentSocket\.onclose[\s\S]{0,260}replaceWebsocketFrame\(''\)/)
  assert.match(cameraFeed, /clearReconnectTimer\(\);[\s\S]{0,180}const currentSocket = new WebSocket\(frameWebSocketUrl\)/)
  assert.match(cameraFeed, /socket !== currentSocket/)
  assert.match(cameraFeed, /document\.visibilityState === 'hidden'[\s\S]{0,100}clearReconnectTimer\(\)/)
  assert.doesNotMatch(cameraFeed, /window\.setInterval/)
  assert.doesNotMatch(cameraFeed, /if \(frameUrl\) requestNextFrame\(\)/)
  assert.match(cameraFeed, /window\.setTimeout\(requestNextFrame, 120\)/)
  assert.match(cameraFeed, /document\.visibilityState === 'hidden'/)
  assert.match(cameraFeed, /onLoad=\{\(\) => \{ setStreamFailed\(false\); if \(!websocketFrameUrl\) scheduleNextFrame\(\); \}\}/)
  assert.match(cameraFeed, /onError=\{\(\) => \{ setStreamFailed\(true\); if \(!websocketFrameUrl\) scheduleNextFrame\(800\); \}\}/)
  assert.doesNotMatch(cameraFeed, /inspectionActive/)
  assert.doesNotMatch(cameraFeed, /setInterval\(\(\) => setFrameRevision/)
  assert.match(cameraFeed, /frameReady && sessionId !== undefined && operationId/)
  assert.match(cameraFeed, /API_BASE_URL.*agent\/live-frame\.jpg/)
  assert.match(cameraFeed, /session_id=\$\{encodeURIComponent\(String\(sessionId\)\)\}/)
  assert.match(cameraFeed, /operation_id=\$\{encodeURIComponent\(operationId\)\}/)
  assert.match(cameraFeed, /websocketFrameUrl \|\| refreshedFallbackUrl/)
  assert.match(cameraFeed, /displayedFrameUrl && <img/)
  const loadAgentData = app.slice(app.indexOf('const loadAgentData'), app.indexOf('useEffect(() => {\n    void loadCloudData'))
  assert.match(loadAgentData, /API_BASE_URL.*agent\/telemetry/)
  assert.doesNotMatch(loadAgentData, /AGENT_API_URL/)
  assert.match(loadAgentData, /hermesState === 'COMPLETED' \|\| missionState === 'COMPLETED'/)
  assert.match(loadAgentData, /hermesState === 'STOPPED' \|\| missionState === 'STOPPED'/)
  assert.match(loadAgentData, /\['ERROR', 'FAILED', 'BLOCKED', 'ABORTED'\]/)
  assert.match(loadAgentData, /telemetryMatchesInspection/)
  assert.match(loadAgentData, /String\(telemetrySessionId\) === String\(expectedSessionId\)/)
  assert.match(loadAgentData, /String\(telemetryOperationId\) === String\(expectedOperationId\)/)
  assert.match(loadAgentData, /setAgentIdentity\(\{ sessionId: telemetrySessionId, operationId: telemetryOperationId \}\)/)
  assert.match(app, /sessionId=\{inspection\?\.sessionId \?\? agentIdentity\.sessionId\}/)
  assert.match(app, /operationId=\{inspection\?\.operationId \?\? agentIdentity\.operationId\}/)
  assert.match(loadAgentData, /const telemetryUpdatedAt = parseUtcTimestamp\(textValue\(telemetry\.updatedAt, telemetry\.updated_at\)\)/)
  assert.match(app, /const parseUtcTimestamp = \(value\?: string\)/)
  assert.match(app, /const detectedTimestamp = parseUtcTimestamp\(detectedAt\)/)
  assert.match(app, /hasTimezone = \/\[zZ\]\$\|\[\+\-\]\\d\{2\}:\\d\{2\}\$\//)
  assert.match(loadAgentData, /if \(!telemetryMatchesInspection\)[\s\S]{0,120}setAgentLive\(\{\}\)/)
  assert.match(loadAgentData, /Date\.now\(\) - telemetryUpdatedAt > 15_000/)
  assert.match(loadAgentData, /requestId === agentRequestRef\.current/)
  assert.match(loadAgentData, /if \(!requestIsCurrent\(\)\) return/)
  assert.match(app, /const merged: LiveData = configuredInspection \? \{\} : \{ \.\.\.serverLive \}/)
  assert.match(loadAgentData, /catch \(error\)[\s\S]{0,100}setAgentLive\(\{\}\)[\s\S]{0,100}setAiSummary\(idleAiSummary\(\)\)/)
  assert.doesNotMatch(loadAgentData, /setAgentLive\(\(current\)/)
  const submit = app.slice(app.indexOf('const submitInspectionSetup'), app.indexOf('// 고른 점검 위치'))
  assert.match(submit, /setAgentLive\(\{\}\)/)
  assert.match(submit, /setAiSummary\(idleAiSummary\(\)\)/)
  assert.match(submit, /previousMissionStateRef\.current = undefined/)
  assert.match(submit, /setInspectionResultOpen\(false\)/)
  assert.match(submit, /setCompletionRunningIdentity\(null\)/)

  const completion = app.slice(app.indexOf('const autonomousCompletionInFlightRef'), app.indexOf('// 미션 초기화'))
  const cloudComplete = completion.indexOf("body: JSON.stringify({ status: 'COMPLETED' })")
  const openResult = completion.indexOf('setInspectionResultOpen(true)', cloudComplete)
  assert.ok(cloudComplete >= 0)
  assert.ok(openResult > cloudComplete)
  assert.match(completion, /for \(let attempt = 0; attempt < 3; attempt \+= 1\)/)
  assert.match(completion, /completionRunningIdentity/)
  assert.match(completion, /shouldAcceptAutonomousCompletion/)

  assert.match(loadAgentData, /explicitRunningInspectionIdentity/)
  assert.match(loadAgentData, /setCompletionRunningIdentity/)

  const cloudPolling = app.slice(app.indexOf('const loadCloudData'), app.indexOf('const loadAgentData'))
  assert.match(cloudPolling, /requestId === cloudRequestRef\.current/)
  assert.match(cloudPolling, /if \(!requestIsCurrent\(\)\) return/)

  const agentStop = submit.indexOf("sendRemoteHermesCommand(\n            'STOP'")
  const cloudAbort = submit.indexOf("body: JSON.stringify({ status: 'ABORTED' })")
  assert.ok(agentStop >= 0)
  assert.ok(cloudAbort > agentStop)
})

test('realtime dashboard is driven by the integrated agent canonical state', async () => {
  const monitor = await source('src/components/RealTimeMonitor.tsx')

  assert.match(monitor, /agentWebSocketUrl/)
  assert.match(monitor, /postMissionAction/)
  assert.match(monitor, /eventToInspectionLog/)
  assert.match(monitor, /missionUiState/)
  assert.match(monitor, /결함 탐지 제외/)
  assert.match(monitor, /resultUi\.distanceLabel/)
  assert.match(monitor, /agentMission\.agentReason/)
  assert.match(monitor, /setInspectionSection\(mission\.area\)/)
  assert.match(monitor, /readAgentLogs/)
  assert.match(monitor, /readDrive/)
  assert.match(monitor, /payload\.type === 'drive\.command'/)
  assert.match(monitor, /현재 인식 모델/)
  assert.match(monitor, /현재 주행 명령/)
  assert.match(monitor, /const missionActive =/)
  assert.match(monitor, /status === '막힘 종료'/)
  assert.match(monitor, /새로운 탐사 시작/)
  assert.match(monitor, /현재 좌·우 명령값/)
  assert.match(monitor, /AI 판단 로그/)
  assert.match(monitor, /초음파 상태/)
  assert.match(monitor, /장애물 인식 상태/)
  assert.match(monitor, /AI 회피 판단/)
  assert.match(monitor, /payload\.type === 'perception\.result'/)
  assert.match(monitor, /payload\.type === 'planner\.decision'/)
  assert.doesNotMatch(monitor, /좌·우 출력 제한값/)
  assert.match(monitor, /const \[defects, setDefects\] = useState<InspectionItem\[\]>\(\[\]\)/)
})

test('dashboard exposes a Qwen-only progress announcement channel separate from AI logs', async () => {
  const monitor = await source('src/components/RealTimeMonitor.tsx')

  assert.match(monitor, /readProgressSummary/)
  assert.match(monitor, /progressAnnouncementUiState/)
  assert.match(monitor, /payload\.type === 'progress\.summary'/)
  assert.match(monitor, /Qwen2\.5-VL/)
  assert.doesNotMatch(monitor, /\['SUMMARY', 'PROGRESS', 'MISSION'\]\.includes\(category\)/)
  assert.doesNotMatch(monitor, /setProgressSummary\(mission\.message\.trim\(\)\)/)
  assert.match(monitor, /AI 판단 로그/)
})

test('dashboard shows cloud Qwen answers in the progress summary card for every active inspection state', async () => {
  const app = await source('src/App.tsx')
  const topCards = app.slice(app.indexOf('function TopCards'), app.indexOf('function CameraFeed'))
  const telemetryReader = app.slice(app.indexOf('const loadAgentData'), app.indexOf('// 클라우드 데이터를 주기적으로'))

  assert.match(telemetryReader, /telemetry\.qwenSummary \?\? telemetry\.qwen_summary/)
  assert.match(telemetryReader, /setAiSummary\(\{/)
  assert.match(topCards, /<DetectionSummary summary=\{summary\} inspectionActive=\{inspectionActive\}/)
  // 목록을 통째로 문자 그대로 맞추면 상태를 하나 더할 때마다 깨진다. 이 검사의
  // 의도는 「진행 중으로 치는 상태에서 요약 카드가 살아 있는가」이므로 그 상태들이
  // 목록에 있는지만 본다.
  //
  // EMERGENCY_STOP 이 들어 있어야 한다. 로봇이 스스로 멈춘 뒤에도 점검은 아직 열려
  // 있고, 운영자가 「점검 종료」를 눌러야 세션이 닫힌다. 이걸 빼면 그 버튼이 잠겨
  // 세션을 닫을 방법이 없어진다.
  for (const state of ['PREPARING', 'RUNNING', 'PAUSED', 'BLOCKED', 'STOPPING', 'RETURNING', 'DRIVING', 'EMERGENCY_STOP']) {
    assert.match(topCards, new RegExp(`'${state}'[^\\]]*\\]\\.includes\\(missionState\\)`))
  }
})

test('dashboard keeps the last successful Qwen answer and hides generation failures', async () => {
  const app = await source('src/App.tsx')

  assert.doesNotMatch(app, /AI 요약 실패/)
  assert.match(app, /summaryStatus === 'error' \|\| summaryStatus === 'unavailable'/)
  assert.match(app, /if \(!summaryFailed\) \{[\s\S]*?setAiSummary\(/)
})


test('dashboard exposes a server-backed V1/V2 navigation selector', async () => {
  const monitor = await source('src/components/RealTimeMonitor.tsx')

  assert.match(monitor, /readNavigation/)
  assert.match(monitor, /setNavigationVersion/)
  assert.match(monitor, /handleNavigationVersionChange/)
  assert.match(monitor, /주행 버전 선택/)
  assert.match(monitor, /V1 규칙 기반/)
  assert.match(monitor, /V2 AI 판단/)
  assert.match(monitor, /const navigationSelectable = agentMission\.state === 'IDLE'/)
  assert.match(monitor, /disabled=\{!navigationSelectable \|\| navigationChanging/)
  assert.match(monitor, /장애물 통과 없음/)
})

test('terminal mission exposes report generation and a new exploration action', async () => {
  const monitor = await source('src/components/RealTimeMonitor.tsx')

  assert.match(
    monitor,
    /\{missionTerminal && \([\s\S]*?onClick=\{handleResetMission\}[\s\S]*?새로운 탐사 시작[\s\S]*?\)\}/,
  )
  assert.match(monitor, /onClick=\{handleCreateReportAndReset\}/)
  assert.match(monitor, />\s*보고서 생성\s*</)
  assert.doesNotMatch(monitor, /보고서 생성 미연동/)
})

test('main feed uses the YOLO annotated stream without conflating Pi camera health', async () => {
  const monitor = await source('src/components/RealTimeMonitor.tsx')

  assert.match(monitor, /const YOLO_STREAM_URL = yoloStreamUrl\(import\.meta\.env\)/)
  assert.match(monitor, /src=\{YOLO_STREAM_URL\}/)
  assert.match(monitor, /alt="YOLO 장애물 탐지 영상"/)
  assert.match(monitor, /className="w-full h-full object-contain select-none"/)
  assert.doesNotMatch(monitor, /5cm 이하는 Pi 로컬 하드 STOP/)
  assert.match(monitor, />\s*YOLO 장애물 탐지 영상\s*</)
  assert.match(monitor, /원본 카메라 \(Pi\)/)
  assert.match(monitor, /cameraConnected \? 'bg-emerald-500' : 'bg-red-500'/)
  assert.match(monitor, /YOLO 주석 스트림/)
  assert.match(monitor, /readCameraHealth\(fetch, import\.meta\.env\)/)
  assert.match(monitor, /readYoloStatus\(fetch, import\.meta\.env\)/)
  assert.match(monitor, /const \[yoloStreamConnected, setYoloStreamConnected\]/)
  assert.doesNotMatch(monitor, /onLoad=\{\(\) => setCameraConnected\(true\)\}/)
  assert.match(monitor, /YOLO 모델/)
  assert.match(monitor, /YOLO 검출/)
  assert.match(monitor, /YOLO 추론 지연/)
  assert.match(monitor, /YOLO 오류/)
})

test('dashboard does not require Google Fonts at runtime', async () => {
  const html = await source('index.html')
  assert.doesNotMatch(html, /fonts\.googleapis\.com/)
  assert.doesNotMatch(html, /fonts\.gstatic\.com/)
})
