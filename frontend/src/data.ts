import { InspectionItem, ReportMeta } from './types';

export const SEWER_IMAGES = {
  exampleJoint: '/local-media/find_images/%EC%9D%B4%EC%9D%8C%EC%83%88%20%EA%B2%B0%ED%95%A8.png',
  exampleScreenshot: '/local-media/find_images/%EC%8A%A4%ED%81%AC%EB%A6%B0%EC%83%B7%202026-07-20%20153227.png',
  exampleSediment: '/local-media/find_images/%ED%87%B4%EC%A0%81%EB%AC%BC.jpg',
  // Image 5: 이음부 결합 (Joint seam misalignment) - Shown in detail modal popup!
  image5: '/local-media/find_images/%EC%9D%B4%EC%9D%8C%EC%83%88%20%EA%B2%B0%ED%95%A8.png',
  
  // Image 4: 표면 균열 (Surface concrete crack)
  image4: '/local-media/find_images/%EC%8A%A4%ED%81%AC%EB%A6%B0%EC%83%B7%202026-07-20%20153227.png',
  
  // Image 6: 실시간 하수도 터널 (Sewer tunnel looking down) - Main Live View CCTV Stream background!
  image6: '/input_file_2.png',
  
  // Image 7: 이물질 누적 및 장애물 (Silt / debris accumulation blockage)
  image7: '/local-media/find_images/%ED%87%B4%EC%A0%81%EB%AC%BC.jpg',
  
  // Image 8: 관 벽면 부식 및 부적합 (Corrosion and rust buildup)
  image8: '/local-media/find_images/%EC%8A%A4%ED%81%AC%EB%A6%B0%EC%83%B7%202026-07-20%20153227.png',
  
  // Image 9: 수목 뿌리 침투 (Tree roots penetrating pipe joint)
  image9: '/local-media/find_images/%EC%8A%A4%ED%81%AC%EB%A6%B0%EC%83%B7%202026-07-20%20153227.png',
};

export const MOCK_REPORTS: ReportMeta[] = [
  {
    id: 'REP-20260721-001',
    region: '광주광역시 하남산단 탐사 예시',
    date: '2026-07-21',
    duration: '00:42:18',
    totalInspected: 2,
    criticalCount: 1,
    healthScore: 82.0,
    gradeZone: 'B 구역',
    inspectionLogs: [
      { time: '10:42:18', text: '탐사 주행 종료 및 출발 지점 안전 복귀 완료', type: 'success' },
      { time: '10:31:44', text: '전방 장애물 감지 후 감속 및 우회 주행 완료', type: 'warn' },
      { time: '10:27:08', text: '관 내부 이상 징후 수동 기록 및 화면 캡처 저장', type: 'alert' },
      { time: '10:18:51', text: '부유 잔해물 감지, 좌측 안전 경로로 회피 완료', type: 'warn' },
      { time: '10:14:32', text: '이음새 결함 감지 및 정밀 촬영 수행', type: 'alert' },
      { time: '10:00:00', text: '센서 교정 완료, 실시간 탐사 주행 시작', type: 'info' },
    ],
  },
  {
    id: 'REP-20260720-001',
    region: '광주광역시 하남산단',
    date: '2026-07-20',
    duration: '01:45:20',
    totalInspected: 2482,
    criticalCount: 18,
    healthScore: 84.5,
    gradeZone: 'B 구역',
  },
  {
    id: 'REP-20260719-002',
    region: '광주광역시 하남산단 2구역',
    date: '2026-07-19',
    duration: '02:10:15',
    totalInspected: 1945,
    criticalCount: 12,
    healthScore: 88.2,
    gradeZone: 'A 구역',
  },
  {
    id: 'REP-20260715-001',
    region: '광주광역시 하남산단 4구역',
    date: '2026-07-15',
    duration: '01:15:40',
    totalInspected: 1240,
    criticalCount: 22,
    healthScore: 79.8,
    gradeZone: 'C 구역',
  }
];

export const MOCK_INSPECTIONS: InspectionItem[] = [
  {
    id: 'INSP-20260721-001', defectType: '균열', severity: 'critical', date: '2026-07-21', time: '10:14:32',
    location: 'MH-22905 ~ MH-22439', imageUrl: SEWER_IMAGES.exampleJoint, reportId: 'REP-20260721-001', confidence: 96.4,
    aiModel: 'SewerNet-V4.2', status: '검토 필요', coordinates: '37.4624, 126.8268', camId: 'SW-D04-021',
    originalImageId: '#EXAMPLE_0721_01', recommendation: '이음새 벌어짐이 확인되어 누수 여부 정밀 점검과 부분 보수가 필요합니다.',
    timeline: [
      { id: 'T21-1-A', time: '10:13:58', title: '접합부 접근', desc: '감속 후 관 연결부 정밀 관찰을 시작했습니다.', distance: 30.8, iconType: 'normal' },
      { id: 'T21-1', time: '10:14:32', title: '균열 포착', desc: '관 연결부 주변의 균열을 확인했습니다.', distance: 31.2, iconType: 'capture' },
      { id: 'T21-1-B', time: '10:15:06', title: '결함 구간 통과', desc: '영상을 기록하고 안전 속도로 주행을 재개했습니다.', distance: 31.7, iconType: 'normal' }
    ]
  },
  {
    id: 'INSP-20260721-002', defectType: '표면손상', severity: 'warning', date: '2026-07-21', time: '10:27:08',
    location: 'MH-22439 ~ MH-22102', imageUrl: SEWER_IMAGES.exampleScreenshot, reportId: 'REP-20260721-001', confidence: 92.1,
    aiModel: 'SewerNet-V4.2', status: '미처리', coordinates: '37.4619, 126.8255', camId: 'SW-D04-021',
    originalImageId: '#EXAMPLE_0721_02', recommendation: '관 내부 이상 부위를 세척한 뒤 재촬영하여 상태 변화를 확인해야 합니다.',
    timeline: [
      { id: 'T21-2-A', time: '10:26:35', title: '이상 징후 접근', desc: '관 내부 시야 이상을 확인하고 주행 속도를 낮췄습니다.', distance: 58.1, iconType: 'normal' },
      { id: 'T21-2', time: '10:27:08', title: '수동 결함 포착', desc: '현장 작업자가 이상 화면을 기록했습니다.', distance: 58.6, iconType: 'capture' },
      { id: 'T21-2-B', time: '10:27:41', title: '장애물 회피 완료', desc: '전방 장애물을 우회한 뒤 정상 경로로 복귀했습니다.', distance: 59.0, iconType: 'normal' }
    ]
  },
  {
    id: 'INSP-001',
    defectType: '균열',
    severity: 'critical',
    date: '2026-07-20',
    time: '13:38:34',
    location: 'MH-22905 ~ MH-22439',
    imageUrl: SEWER_IMAGES.image5, // Image 5 detail popup
    reportId: 'REP-20260720-001',
    confidence: 94.8,
    aiModel: 'SewerNet-V4.2',
    status: '검토 필요',
    coordinates: '37.4624, 126.8268',
    camId: 'SW-D04-012',
    originalImageId: '#IMG_24905',
    recommendation: '이음부 간격이 허용 범위를 초과하여 누수 및 토사 유입의 위험이 높습니다. 3개월 이내 부분 비굴착 보수 공사(CIPP 패커 공법)를 권장합니다.',
    timeline: [
      {
        id: 'T1-1',
        time: '13:30:15',
        title: '로봇 시작 (10.00m)',
        desc: '시스템 체크: 모든 센서 정상. 기동성 100%.',
        distance: 10.00,
        iconType: 'start'
      },
      {
        id: 'T1-2',
        time: '13:34:22',
        title: '장애물 감지 (25.00m)',
        desc: '부유 잔해물 확인. 로봇 성공적으로 우회함.',
        distance: 25.00,
        iconType: 'hazard'
      },
      {
        id: 'T1-3',
        time: '13:38:34',
        title: '균열 포착 (41.70m)',
        desc: 'AI 신뢰도 94.8%. 관 연결부 주변 균열을 확인함.',
        distance: 41.70,
        iconType: 'capture'
      }
    ]
  },
  {
    id: 'INSP-002',
    defectType: '막힘',
    severity: 'warning',
    date: '2026-07-20',
    time: '13:32:11',
    location: 'MH-22439 ~ MH-22102',
    imageUrl: SEWER_IMAGES.image7, // Image 7 as requested!
    reportId: 'REP-20260720-001',
    confidence: 91.2,
    aiModel: 'SewerNet-V4.2',
    status: '미처리',
    coordinates: '37.4619, 126.8255',
    camId: 'SW-D04-010',
    originalImageId: '#IMG_24810',
    recommendation: '퇴적물이 관로 저부의 30% 이상을 차지하여 배수 단면을 축소시키고 있습니다. 고압 준설차를 이용한 세정 작업을 권고합니다.',
    timeline: [
      {
        id: 'T2-1',
        time: '13:28:00',
        title: '로봇 재개 (30.00m)',
        desc: '준설 상태 평가 모드 진입. 속도 0.20m/s.',
        distance: 30.00,
        iconType: 'start'
      },
      {
        id: 'T2-2',
        time: '13:32:11',
        title: '막힘 감지 (35.42m)',
        desc: '슬러지 및 자갈 더미가 쌓여 유속을 약화시키고 있음.',
        distance: 35.42,
        iconType: 'capture'
      }
    ]
  },
  {
    id: 'INSP-003',
    defectType: '균열',
    severity: 'warning',
    date: '2026-07-20',
    time: '13:24:08',
    location: 'MH-22102 ~ MH-21840',
    imageUrl: SEWER_IMAGES.image8, // Image 8 as requested!
    reportId: 'REP-20260720-001',
    confidence: 93.5,
    aiModel: 'SewerNet-V4.2',
    status: '담당자 배정',
    coordinates: '37.4611, 126.8241',
    camId: 'SW-D04-008',
    originalImageId: '#IMG_24750',
    recommendation: '구조물 내력 분산으로 미세 균열이 여러 갈래 발생했습니다. 즉각적인 붕괴 위험은 없으나 지하수 침투 가능성이 있으므로 주기적인 모니터링 및 균열 게이지 부착을 권장합니다.',
    timeline: [
      {
        id: 'T3-1',
        time: '13:15:00',
        title: '로봇 시작 (20.00m)',
        desc: '센서 수동 교정 완료 후 기동.',
        distance: 20.00,
        iconType: 'start'
      },
      {
        id: 'T3-2',
        time: '13:24:08',
        title: '구조적 균열 포착 (27.15m)',
        desc: '미세 종방향 표면 균열 발견. 모니터링 권장.',
        distance: 27.15,
        iconType: 'capture'
      }
    ]
  },
  {
    id: 'INSP-004',
    defectType: '표면손상',
    severity: 'warning',
    date: '2026-07-20',
    time: '14:15:10',
    location: 'MH-22439 ~ MH-22102',
    imageUrl: SEWER_IMAGES.image4, // Image 4
    reportId: 'REP-20260720-001',
    confidence: 89.2,
    aiModel: 'SewerNet-V4.2',
    status: '담당자 배정',
    coordinates: '37.4615, 126.8248',
    camId: 'SW-D04-011',
    originalImageId: '#IMG_24888',
    recommendation: '구조물 내력 분산으로 미세 균열이 여러 갈래 발생했습니다. 즉각적인 붕괴 위험은 없으나 지하수 침투 가능성이 있으므로 주기적인 모니터링 및 균열 게이지 부착을 권장합니다.',
    timeline: [
      {
        id: 'T4-1',
        time: '14:05:00',
        title: '로봇 재개 (42.00m)',
        desc: '이상 확인 후 재점검 완료. 속도 0.15m/s 설정.',
        distance: 42.00,
        iconType: 'start'
      },
      {
        id: 'T4-2',
        time: '14:15:10',
        title: '표면손상 감지 (68.30m)',
        desc: '관 벽면의 표면손상을 확인했습니다. 주기적인 모니터링을 권장합니다.',
        distance: 68.30,
        iconType: 'capture'
      }
    ]
  },
  {
    id: 'INSP-005',
    defectType: '막힘',
    severity: 'critical',
    date: '2026-07-20',
    time: '15:10:18',
    location: 'MH-21510 ~ MH-21200',
    imageUrl: SEWER_IMAGES.image9, // Image 9 as requested!
    reportId: 'REP-20260720-001',
    confidence: 98.4,
    aiModel: 'SewerNet-V4.2',
    status: '미처리',
    coordinates: '37.4590, 126.8210',
    camId: 'SW-D04-015',
    originalImageId: '#IMG_25100',
    recommendation: '외부 가로수 뿌리가 이음부 틈새를 뚫고 관 내부로 왕성하게 침투하여 관로를 50% 이상 차단하고 있습니다. 회전 기계식 뿌리 제거 커터를 투입한 후 즉각 수밀 보수 공사를 수행해야 합니다.',
    timeline: [
      {
        id: 'T5-1',
        time: '15:01:00',
        title: '로봇 전진 (130.00m)',
        desc: '관로 방향 전환 유도. 센서 열상 감지 가동.',
        distance: 130.00,
        iconType: 'start'
      },
      {
        id: 'T5-2',
        time: '15:10:18',
        title: '막힘 장애물 포착 (145.10m)',
        desc: '장애물이 관 단면을 막아 대규모 통수 저해가 발생했습니다.',
        distance: 145.10,
        iconType: 'capture'
      }
    ]
  }
];
