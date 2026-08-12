// 우선순위 지도 드릴다운 e2e — `npm test` 에는 포함되지 않습니다 (.e2e.mjs).
//
// 필요한 것: vite dev 서버뿐입니다 (프록시 기본값이 배포 API). 실행:
//
//   node tests/priority-map.e2e.mjs
//   E2E_BASE_URL=http://localhost:3100 node tests/priority-map.e2e.mjs
//
// 지도 컴포넌트가 DEV 모드에서 노출하는 window.__priorityMap 으로
// 경위도→픽셀을 변환해 실제 클릭 시나리오를 돌립니다:
// 전국(시도) → 서울 드릴다운 → 시군구 상세 → 읍면동 뷰 → 복귀.
import { chromium } from 'playwright';

const BASE = process.env.E2E_BASE_URL ?? 'http://localhost:3000';

const browser = await chromium.launch({
  args: ['--use-gl=swiftshader', '--disable-gpu-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });

let failures = 0;
const check = (label, ok, detail = '') => {
  console.log(`  [${ok ? 'OK  ' : 'FAIL'}] ${label}${ok ? '' : `  ${detail}`}`);
  if (!ok) failures += 1;
};

const errors = [];
page.on('pageerror', (err) => errors.push(String(err)));

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
// 셸에 따라 메뉴 이름이 다릅니다 (탭 헤더는 "우선순위 지도", 사이드바는 "우선순위").
await page.getByRole('button', { name: /우선순위/ }).first().click();
await page.waitForFunction(() => {
  const map = window.__priorityMap;
  return map && map.isStyleLoaded() && map.getSource('districts');
}, { timeout: 20000 });
await page.waitForTimeout(2500);
check(
  '전국 뷰 로드 (폴리곤 렌더)',
  await page.evaluate(
    () => window.__priorityMap.queryRenderedFeatures({ layers: ['districts-fill'] }).length > 0,
  ),
);
check(
  '시도 라벨 렌더 (자체 라벨 — 배경 타일에는 지명이 없음)',
  await page.evaluate(
    () => window.__priorityMap.queryRenderedFeatures({ layers: ['sido-labels'] }).length > 0,
  ),
);
check(
  '이동 제한(maxBounds) 설정됨',
  await page.evaluate(() => window.__priorityMap.getMaxBounds() !== null),
);
// 더블클릭이 click 을 두 번 발화시켜 "선택 → 해제"가 되는 것을 막습니다.
check(
  '더블클릭 줌 비활성',
  await page.evaluate(() => !window.__priorityMap.doubleClickZoom.isEnabled()),
);

async function clickLngLat(lng, lat) {
  const point = await page.evaluate(
    ([x, y]) => {
      const p = window.__priorityMap.project([x, y]);
      const rect = window.__priorityMap.getContainer().getBoundingClientRect();
      return { x: rect.left + p.x, y: rect.top + p.y };
    },
    [lng, lat],
  );
  await page.mouse.click(point.x, point.y);
}

// 서울 드릴다운
await clickLngLat(126.98, 37.55);
await page.waitForTimeout(1400);
check('서울 드릴다운', (await page.textContent('body')).includes('서울특별시 시군구 순위'));
check(
  '시군구 라벨 렌더 (드릴다운한 시도만)',
  await page.evaluate(
    () => window.__priorityMap.queryRenderedFeatures({ layers: ['sgg-labels'] }).length > 0,
  ),
);

// 중구 상세 → 읍면동
await clickLngLat(126.997, 37.56);
await page.waitForSelector('text=이 구역이 위험한 이유', { timeout: 8000 });
check('시군구 상세 패널', true);
check('순위 변화 표기', (await page.textContent('body')).includes('순위 변화'));
// 평시 화면에는 강수 정보가 아예 없어야 합니다 — 강수 반영 모드와 완전 분리.
check(
  '평시 상세에는 강수 정보 미표시',
  !(await page.textContent('body')).includes('최근 30일 비'),
);
// ── 강수 반영 보기: 색·목록·상세가 바뀌고 참고용 띠가 뜨며, 끄면 원상복구 ──
await page.getByRole('button', { name: '강수 반영', exact: true }).click();
await page.waitForTimeout(700);
check(
  '강수 모드: 상세에 강수 블록 표시',
  (await page.textContent('body')).includes('최근 30일 비'),
);
// 툴팁으로는 "순위에 반영하지 않는다"가 남아 있어야 오해를 풀 수 있습니다.
const rainCaveat = await page.evaluate(() => {
  const line = [...document.querySelectorAll('p[title]')].find((el) =>
    (el.textContent ?? '').includes('강수 반영 시'),
  );
  return line ? line.getAttribute('title') ?? '' : null;
});
check(
  '강수 반영 점수는 참고값임을 툴팁으로 설명',
  rainCaveat === null || rainCaveat.includes('반영하지 않'),
  String(rainCaveat),
);
check(
  '강수 모드: 참고용 띠 표시',
  (await page.textContent('body')).includes('최근 30일 강수 반영'),
);
check(
  '강수 모드: 지도 색이 triggeredColor 로 전환',
  await page.evaluate(() =>
    JSON.stringify(
      window.__priorityMap.getPaintProperty('districts-fill', 'fill-color'),
    ).includes('triggeredColor'),
  ),
);
// 목록 제목은 상세를 닫아야 보입니다 (상세가 열리면 목록 대신 상세가 렌더).
await page.getByRole('button', { name: '상세 닫기' }).click();
await page.waitForTimeout(600);
check(
  '강수 모드: 목록이 강수 반영 순위로 전환',
  (await page.textContent('body')).includes('강수 반영 순위'),
);
// 불변식: 강수 모드의 등급 배지 = 표시된 전국(triggered) 순위를 공식 등급과
// 같은 상대배분(10/20/40/20/10)으로 접은 값. 지도 색과 글자가 같이 움직여야
// 하고, 특정 구가 몇 등급인지(개수 기대값)는 검사하지 않습니다.
const rainBadgeCheck = await page.evaluate(async () => {
  const res = await fetch('/backend-api/priority/districts?limit=300');
  const data = await res.json();
  const total = data.districts.filter((d) => d.triggered_score != null).length;
  const labelOf = { 1: '양호', 2: '관찰', 3: '보통', 4: '우선', 5: '최우선' };
  const fold = (rank) => {
    const pct = rank / total;
    return pct <= 0.1 ? 5 : pct <= 0.3 ? 4 : pct <= 0.7 ? 3 : pct <= 0.9 ? 2 : 1;
  };
  const rows = [...document.querySelectorAll('li button')]
    .map((btn) => {
      const nat = [...btn.querySelectorAll('span')]
        .map((s) => s.textContent ?? '')
        .find((t) => t.trim().startsWith('전국 '));
      const badge = btn.querySelector('span.rounded-full');
      if (!nat || !badge) return null;
      return {
        rank: parseInt(nat.replace('전국', '').trim(), 10),
        label: (badge.textContent ?? '').trim(),
      };
    })
    .filter(Boolean);
  const bad = rows.filter((r) => labelOf[fold(r.rank)] !== r.label);
  return { total, rows: rows.length, bad: bad.length, sample: bad[0] ?? null };
});
check(
  '강수 모드: 등급 배지가 triggered 순위의 접힌 등급과 일치',
  rainBadgeCheck.rows > 0 && rainBadgeCheck.bad === 0,
  JSON.stringify(rainBadgeCheck),
);
await page.getByRole('button', { name: '강수 반영', exact: true }).click();
await page.waitForTimeout(700);
check(
  '강수 모드 해제: 공식 등급색 복귀',
  await page.evaluate(() =>
    JSON.stringify(
      window.__priorityMap.getPaintProperty('districts-fill', 'fill-color'),
    ).includes('gradeColor'),
  ),
);
check(
  '강수 모드 해제: 목록이 공식 순위로 복귀',
  (await page.textContent('body')).includes('시군구 순위'),
);

// 이후 흐름(읍면동 보기)을 위해 중구 상세를 다시 엽니다.
await clickLngLat(126.997, 37.56);
await page.waitForSelector('text=이 구역이 위험한 이유', { timeout: 8000 });

// 버튼을 누르기 전에, 클릭만으로 이미 동 경계가 떠 있어야 합니다.
await page.waitForTimeout(1600);
const dongOnSelect = await page.evaluate(
  () => window.__priorityMap.queryRenderedFeatures({ layers: ['dongs-fill'] }).length,
);
check('시군구 클릭만으로 동 경계 표시', dongOnSelect > 0, String(dongOnSelect));

const dongButton = page.getByRole('button', { name: /읍면동 \d+개 보기/ });
await dongButton.waitFor({ timeout: 8000 });
await dongButton.click();
await page.waitForTimeout(2000);
const dongCount = await page.evaluate(
  () => window.__priorityMap.queryRenderedFeatures({ layers: ['dongs-fill'] }).length,
);
check('읍면동 폴리곤 렌더', dongCount > 0, String(dongCount));

// 동 이름은 지금 줌에서 구역 안에 들어가는 것만 그립니다. 하나도 없으면
// 라벨 판정이 너무 엄격하고, 동 수보다 많으면 판정이 아예 안 걸린 것입니다.
const dongLabelShown = await page.evaluate(
  () => (window.__priorityMap.getFilter('dong-labels')?.[2]?.[1] ?? []).length,
);
const dongTotal = Number(
  (await page.textContent('body')).match(/읍면동\s*(\d+)\s*개/)?.[1] ?? 0,
);
check('동 이름 라벨 표시', dongLabelShown > 0, `${dongLabelShown}개`);
// 이름끼리 겹치면 두 동 이름이 한 덩어리로 읽힙니다. 화면 사각형으로 실측.
const labelClashes = await page.evaluate(() => {
  const map = window.__priorityMap;
  const boxes = map
    .queryRenderedFeatures({ layers: ['dong-labels'] })
    .map((feature) => {
      const point = map.project(feature.geometry.coordinates);
      const half = (feature.properties.name.length * 10.5) / 2;
      return { x1: point.x - half, x2: point.x + half, y1: point.y - 5.25, y2: point.y + 5.25 };
    });
  const hits = [];
  for (let i = 0; i < boxes.length; i += 1) {
    for (let j = i + 1; j < boxes.length; j += 1) {
      const a = boxes[i];
      const b = boxes[j];
      if (a.x1 < b.x2 && a.x2 > b.x1 && a.y1 < b.y2 && a.y2 > b.y1) hits.push([i, j]);
    }
  }
  return hits.length;
});
check('동 이름끼리 겹치지 않음', labelClashes === 0, `${labelClashes}건`);
check(
  '이름은 동 수 이내로만 그림 (좁은 동은 생략)',
  dongTotal > 0 && dongLabelShown <= dongTotal,
  `${dongLabelShown}/${dongTotal}`,
);

// 동 클릭 = 선택 하이라이트 토글
await clickLngLat(126.997, 37.56);
await page.waitForTimeout(700);
check(
  '동 클릭 선택 하이라이트',
  await page.evaluate(
    () =>
      window.__priorityMap.queryRenderedFeatures({ layers: ['dong-selected-fill'] })
        .length > 0,
  ),
);
await clickLngLat(126.997, 37.56);
await page.waitForTimeout(700);
check(
  '같은 동 재클릭 시 선택 해제',
  (await page.evaluate(
    () =>
      window.__priorityMap.queryRenderedFeatures({ layers: ['dong-selected-fill'] })
        .length,
  )) === 0,
);
// 재클릭은 동 목록도 접고 시군구 상세로 돌려놓습니다 — 취소했는데 그 동의
// 정보가 계속 떠 있으면 무엇이 선택된 상태인지 알 수 없습니다.
check(
  '같은 동 재클릭 시 시군구 상세로 복귀',
  (await page.textContent('body')).includes('이 구역이 위험한 이유'),
);

// 지도에서 동을 클릭하면 우측도 동 목록으로 맞춰지고, 고른 행이 스크롤
// 안쪽에 보여야 합니다 (목록이 침하 순이라 아래쪽 동은 가려집니다).
await clickLngLat(126.997, 37.56);
await page.waitForTimeout(1400);
const syncState = await page.evaluate(() => {
  const list = document.querySelector('ul.overflow-y-auto');
  const row = list?.querySelector('li button[aria-pressed="true"]');
  if (!list || !row) return { panel: !!list, row: false };
  const lr = list.getBoundingClientRect();
  const rr = row.getBoundingClientRect();
  return { panel: true, row: true, visible: rr.top >= lr.top - 1 && rr.bottom <= lr.bottom + 1 };
});
check('지도에서 동 클릭 시 우측이 동 목록으로 동기화', syncState.panel && syncState.row, JSON.stringify(syncState));
check('선택한 동 행이 목록 안에 보임', syncState.visible === true, JSON.stringify(syncState));

// 동 이름을 우리가 그리는 동안에는 배경 상세 타일을 걷어냅니다 — 타일에도
// 동 이름이 박혀 있어 같은 이름이 두 번 겹칩니다.
const detailOpacity = await page.evaluate(() =>
  window.__priorityMap.getLayer('basemap-detail')
    ? window.__priorityMap.getPaintProperty('basemap-detail', 'raster-opacity')
    : 0,
);
check('동 뷰에서 상세 타일 지명 제거', detailOpacity === 0, JSON.stringify(detailOpacity));

// 동 목록 클릭 = 선택 + 그 동 경계로 줌 (좌표는 클라이언트의 동 GeoJSON에서)
const zoomBeforeFocus = await page.evaluate(() => window.__priorityMap.getZoom());
await page
  .getByRole('button', { name: /확인된 이력 없음|침하 \d+건/ })
  .first()
  .click();
await page.waitForTimeout(900);
check(
  '동 목록 클릭 시 그 동으로 줌',
  (await page.evaluate(() => window.__priorityMap.getZoom())) > zoomBeforeFocus + 0.2,
);
check(
  '동 목록 클릭 시 선택 하이라이트',
  await page.evaluate(
    () =>
      window.__priorityMap.queryRenderedFeatures({ layers: ['dong-selected-fill'] })
        .length > 0,
  ),
);

// 복귀
await page.getByRole('button', { name: '전국' }).click();
await page.waitForTimeout(1200);
check(
  '전국 복귀 시 동 레이어 숨김',
  (await page.evaluate(
    () => window.__priorityMap.queryRenderedFeatures({ layers: ['dongs-fill'] }).length,
  )) === 0,
);

// ── 우측 패널만으로 드릴다운 (지도 클릭 없이 동등한 경로가 있어야 함) ──
await page.getByRole('button', { name: /부산광역시/ }).click();
await page.waitForTimeout(1500);
check(
  '패널 드릴다운: 시군구 목록',
  (await page.textContent('body')).includes('부산광역시 시군구 순위'),
);
await page.getByRole('button', { name: /해운대구/ }).click();
await page.waitForSelector('text=이 구역이 위험한 이유', { timeout: 8000 });
check('패널 드릴다운: 상세 패널', true);
await page.getByRole('button', { name: '상세 닫기' }).click();
await page.getByRole('button', { name: '전국 목록으로 돌아가기' }).click();
await page.waitForTimeout(1000);

// ── 시군구 연타: 이전 선택의 상세가 새 선택 이름 아래 남으면 안 됨 ──
await clickLngLat(126.98, 37.55); // 서울 드릴다운
await page.waitForTimeout(1400);
await clickLngLat(126.997, 37.56); // 중구
await page.waitForSelector('text=이 구역이 위험한 이유', { timeout: 8000 });
await clickLngLat(126.98, 37.585); // 종로구 — 응답을 기다리지 않고 즉시
const rightAfter = await page.textContent('body');
check(
  '연타 직후 이전 구역(중구) 상세가 남지 않음',
  !(rightAfter.includes('이 구역이 위험한 이유') && rightAfter.includes('중구')),
);
await page.waitForSelector('h3:has-text("종로구")', { timeout: 8000 });
check('연타 정착 후 마지막 선택(종로구) 표시', true);

// ── 동 뷰에서 시군구 복귀 버튼 (전국 직행과 다른 경로) ──
const dongButton2 = page.getByRole('button', { name: /읍면동 \d+개 보기/ });
await dongButton2.waitFor({ timeout: 8000 });
await dongButton2.click();
await page.waitForTimeout(1800);
// 동 하나를 확대해 둔 채 복귀 — 카메라도 시군구 화면으로 돌아와야 합니다.
await page
  .getByRole('button', { name: /확인된 이력 없음|침하 \d+건/ })
  .first()
  .click();
await page.waitForTimeout(1100);
const zoomAtDong = await page.evaluate(() => window.__priorityMap.getZoom());
await page.getByRole('button', { name: '시군구 목록으로 돌아가기' }).click();
await page.waitForTimeout(1200);
check(
  '시군구 복귀 시 카메라도 시군구 화면으로 복귀',
  (await page.evaluate(() => window.__priorityMap.getZoom())) < zoomAtDong - 0.2,
  `동 ${zoomAtDong.toFixed(2)} → ${await page.evaluate(() => window.__priorityMap.getZoom().toFixed(2))}`,
);
// 시군구를 고르면 지도는 곧바로 동을 보여 줍니다. 패널만 상세로 돌아오므로
// 동 경계는 그대로 남아 있어야 합니다.
check(
  '동 목록 → 상세 복귀 후에도 동 경계 유지',
  (await page.evaluate(
    () => window.__priorityMap.queryRenderedFeatures({ layers: ['dongs-fill'] }).length,
  )) > 0,
);
check(
  '복귀 후 상세 패널 유지',
  (await page.textContent('body')).includes('이 구역이 위험한 이유'),
);

// ── 지도 → 점검 설정 연계: "점검 시작" (버튼 문구는 8/5 UI 개편으로 개명) ──
// 동을 고르지 않으면 안내, 고르면 그 동이 점검 위치에 채워진 채 모달이 열립니다.
await page.getByRole('button', { name: '점검 시작' }).click();
await page.waitForTimeout(700);
check(
  '동 미선택 시 검사 시작 안내',
  (await page.textContent('body')).includes('검사를 시작할 동을 선택'),
);

const dongButton3 = page.getByRole('button', { name: /읍면동 \d+개 보기/ });
await dongButton3.waitFor({ timeout: 8000 });
await dongButton3.click();
await page.waitForTimeout(1800);
const dongRow = page.getByRole('button', { name: /확인된 이력 없음|침하 \d+건/ }).first();
await dongRow.waitFor({ timeout: 8000 });
// 목록 행은 "종로5·6가동침하 2건" 처럼 이름 뒤에 침하 요약이 붙습니다.
const pickedDong = ((await dongRow.textContent()) ?? '')
  .replace(/(확인된 이력 없음|침하\s*\d+건).*$/, '')
  .trim();
await dongRow.click();
await page.waitForTimeout(800);
await page.getByRole('button', { name: '점검 시작' }).click();
await page.waitForSelector('[role="dialog"]', { timeout: 8000 });
// 점검 위치는 시도까지 붙은 전체 이름으로 넘어갑니다.
const sectionName = await page.inputValue('.inspection-setup-form input');
check(
  '선택한 동이 점검 위치로 넘어감',
  sectionName.endsWith(pickedDong),
  `모달=${sectionName} / 목록=${pickedDong}`,
);
// 구간을 미리 골라 두지 않으면 사용자가 한 번 더 눌러야 시작할 수 있습니다.
const sectionValue = await page.inputValue('.inspection-setup-form select');
check(
  '점검 구간이 미리 선택됨',
  sectionValue.includes(pickedDong),
  `구간=${sectionValue}`,
);
await page.getByRole('button', { name: '팝업 닫기' }).click();
await page.waitForTimeout(400);

// ── 선택 해제하면 동 경계도 사라집니다 (동 표시가 선택에 매여 있음) ──
await page.getByRole('button', { name: '시군구 목록으로 돌아가기' }).click();
await page.waitForTimeout(700);
await page.getByRole('button', { name: '상세 닫기' }).click();
await page.waitForTimeout(1000);
check(
  '선택 해제 시 동 경계 숨김',
  (await page.evaluate(
    () => window.__priorityMap.queryRenderedFeatures({ layers: ['dongs-fill'] }).length,
  )) === 0,
);

// ── 탭 이탈 → 재진입 (과거 "영영 불러오는 중" 버그의 경로) ──
const otherTab = page
  .getByRole('button', { name: /실시간|모니터링|보고서|대시보드/ })
  .first();
if ((await otherTab.count()) > 0) {
  await otherTab.click();
  await page.waitForTimeout(800);
  await page.getByRole('button', { name: /우선순위/ }).first().click();
  await page.waitForFunction(
    () => {
      const map = window.__priorityMap;
      return map && map.isStyleLoaded() && map.getSource('districts');
    },
    { timeout: 20000 },
  );
  await page.waitForTimeout(1500);
  check(
    '탭 재진입 후 지도 재로드 ("불러오는 중"에 멈추지 않음)',
    await page.evaluate(
      () =>
        window.__priorityMap.queryRenderedFeatures({ layers: ['districts-fill'] }).length > 0,
    ),
  );
} else {
  console.log('  [SKIP] 다른 탭을 찾지 못해 재진입 검사를 건너뜁니다');
}

const pageErrors = errors.filter((e) => !e.includes('127.0.0.1:8000'));
check('페이지 오류 없음', pageErrors.length === 0, pageErrors.slice(0, 3).join(' | '));

await browser.close();
console.log(failures ? `실패 ${failures}건` : '전부 통과');
process.exit(failures ? 1 : 0);
