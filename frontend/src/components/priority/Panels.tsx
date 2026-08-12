import { ChevronLeft, ChevronRight, CloudRain, X } from 'lucide-react';
import { useEffect, useRef } from 'react';
import type {
  AdminDong,
  DongListResponse,
  PriorityHistoryResponse,
  PriorityScoreDetail,
  PriorityScoreSummary,
  PrioritySidoSummary,
} from '../../api/priority';
import {
  foldRankToGrade,
  GRADE_COLORS,
  GRADE_LABELS,
  MISSING_BAR,
  sidoColor,
} from './geo';

// 지표 이름은 API(`display_names`)가 주지만, "그게 무슨 뜻인가"는 화면에서 읽는
// 사람을 위한 문구라 여기 둡니다. 용어를 그대로 쓰지 말고 뜻을 풀어 씁니다 —
// 이름만 보고는 무슨 값인지 알 수 없다는 지적이 있었습니다.
const INDICATOR_HELP: Record<string, string> = {
  combined_sewer_ratio: '빗물과 생활하수가 한 관으로 흐르는 구간의 비율 (비 올 때 물살이 급해져 관 손상)',
  aged_ratio_25y: '땅에 묻은 지 25년이 넘은 관로의 비율',
  subsidence_per_km: '관로 1km당 실제로 일어난 땅 꺼짐 사고 건수',
  concrete_ratio: '콘크리트로 만든 관로의 비율 (하수 가스에 부식되기 쉬움)',
  heavy_rain_zone_ratio: '지자체가 집중호우 위험 구역으로 지정한 곳의 맨홀 비율',
  large_diameter_ratio: '지름 500mm 이상 굵은 관로의 비율 (굵을수록 터질 때 피해가 큼)',
  population: '이 구역의 주민등록 인구',
};

// 상위 단계로 돌아가는 버튼. 텍스트 화살표(←)는 lucide 아이콘과 굵기·정렬이
// 어긋나고 `hover:underline` 은 링크로 읽혀서, 아이콘 + 테두리 있는 보조 버튼으로
// 통일합니다. 이름은 목적지 하나뿐이면 화면 어디의 "전국"인지 모호하므로
// aria-label 로 행동을 적습니다.
function BackButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      aria-label={`${label} 목록으로 돌아가기`}
      className="shrink-0 flex items-center gap-0.5 pl-1 pr-2 py-1 text-[11px] font-semibold text-[#52514e] bg-[#f0f0f4] rounded-md hover:bg-[#eef3fd] hover:text-[#0f458f] transition-colors cursor-pointer"
    >
      <ChevronLeft className="w-3.5 h-3.5" />
      {label}
    </button>
  );
}

export function SidoListPanel({
  rows,
  onSelect,
}: {
  rows: PrioritySidoSummary[];
  onSelect: (sidoCode: string) => void;
}) {
  const sorted = [...rows].sort((a, b) => a.rank - b.rank);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 px-4 py-3 border-b border-[#eeeef2]">
        <h3 className="text-sm font-bold text-[#1a1b20]">시도 순위</h3>
        <p className="text-[11px] text-[#898781] mt-0.5">
          관로가 긴 시군구에 비중을 둔 평균 · 누르면 시군구로 이동
        </p>
      </div>
      <ul className="min-h-0 flex-1 overflow-y-auto divide-y divide-[#f4f4f7]">
        {sorted.map((row) => (
          <li key={row.sido_code}>
            <button
              onClick={() => onSelect(row.sido_code)}
              className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-[#f6f8fd] transition-colors cursor-pointer"
            >
              <span className="w-7 text-sm font-bold text-[#0f458f] tabular-nums">
                {row.rank}
              </span>
              <span
                className="inline-block w-3 h-3 rounded-[3px] shrink-0"
                style={{ backgroundColor: sidoColor(row.rank, sorted.length) }}
              />
              <span className="flex-1 min-w-0">
                <span className="block text-sm font-medium text-[#1a1b20] truncate">
                  {row.sido_name}
                </span>
                <span className="block text-[11px] text-[#898781] tabular-nums">
                  평균 위험도 {row.weighted_score.toFixed(3)} · 최우선{' '}
                  {row.grade_counts['5'] ?? 0}/{row.district_count}곳
                </span>
              </span>
              <ChevronRight className="w-4 h-4 text-[#c3c2b7] shrink-0" />
            </button>
          </li>
        ))}
        {sorted.length === 0 && (
          <li className="px-4 py-8 text-center text-sm text-[#898781]">
            시도 집계가 없습니다.
          </li>
        )}
      </ul>
    </div>
  );
}

export function DistrictListPanel({
  title,
  rows,
  rainMode = false,
  triggeredRanks,
  onSelect,
  onBack,
}: {
  title: string;
  rows: PriorityScoreSummary[];
  /** 강수 반영 보기 — 순위 칸을 triggered 순위 + 공식 대비 변동으로 바꿉니다. */
  rainMode?: boolean;
  triggeredRanks?: Map<string, number>;
  onSelect: (code: string) => void;
  onBack: () => void;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center justify-between px-4 py-3 border-b border-[#eeeef2]">
        <h3 className="text-sm font-bold text-[#1a1b20]">{title}</h3>
        <BackButton label="전국" onClick={onBack} />
      </div>
      <ul className="min-h-0 flex-1 overflow-y-auto divide-y divide-[#f4f4f7]">
        {rows.map((row, index) => {
          const triggered = triggeredRanks?.get(row.sgg_code);
          const shift = rainMode && triggered != null ? row.rank - triggered : 0;
          // 강수 모드의 지도 색과 같은 접기 — 배지만 평시 등급이면 화면이 모순됩니다.
          const rainGrade =
            rainMode && triggered != null && triggeredRanks && triggeredRanks.size > 0
              ? foldRankToGrade(triggered, triggeredRanks.size)
              : null;
          return (
          <li key={row.sgg_code}>
            <button
              onClick={() => onSelect(row.sgg_code)}
              className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-[#f6f8fd] transition-colors cursor-pointer"
            >
              {/* w-11 + nowrap: 전국 순위가 세 자리(112 등)면 w-9 에서 "전국"과
                  숫자가 두 줄로 갈라져 행 높이가 들쑥날쑥해집니다. */}
              <span className="w-11 text-center shrink-0">
                <span className="block text-sm font-bold text-[#0f458f] tabular-nums">
                  {rainMode ? index + 1 : (row.sido_rank ?? '—')}
                </span>
                <span className="block text-[10px] text-[#898781] tabular-nums whitespace-nowrap">
                  전국 {rainMode && triggered != null ? triggered : row.rank}
                </span>
              </span>
              <span className="flex-1 min-w-0">
                <span className="block text-sm font-medium text-[#1a1b20] truncate">
                  {row.full_name.split(' ').slice(1).join(' ') || row.full_name}
                </span>
                <span className="block text-[11px] text-[#898781] tabular-nums">
                  {rainMode && row.triggered_score != null ? (
                    <>
                      강수 반영 {row.triggered_score.toFixed(3)}
                      {shift !== 0 && (
                        <span
                          className={
                            shift > 0
                              ? 'ml-1 font-semibold text-[#0f458f]'
                              : 'ml-1 text-[#a5a3a0]'
                          }
                        >
                          {shift > 0 ? `▲${shift}` : `▼${-shift}`}
                        </span>
                      )}
                    </>
                  ) : (
                    <>위험도 {row.score.toFixed(3)}</>
                  )}
                </span>
              </span>
              <span
                className="shrink-0 text-[11px] font-semibold px-2 py-0.5 rounded-full text-white"
                style={{ backgroundColor: GRADE_COLORS[rainGrade ?? row.grade] }}
              >
                {rainGrade != null ? GRADE_LABELS[rainGrade] : row.grade_label}
              </span>
            </button>
          </li>
          );
        })}
        {rows.length === 0 && (
          <li className="px-4 py-8 text-center text-sm text-[#898781]">
            표시할 시군구가 없습니다.
          </li>
        )}
      </ul>
    </div>
  );
}

export function DongListPanel({
  districtName,
  info,
  selectedCode,
  onBack,
  onFocus,
}: {
  districtName: string;
  info: DongListResponse | null;
  selectedCode?: string | null;
  onBack: () => void;
  onFocus: (dong: AdminDong) => void;
}) {
  const rows = info
    ? [...info.dongs].sort((a, b) => b.subsidence_count - a.subsidence_count)
    : [];
  // 목록은 침하 건수 순이라 지도에서 고른 동이 스크롤 아래에 있을 수 있습니다.
  // 하이라이트만 켜고 보여 주지 않으면 눌러도 반응이 없는 것처럼 보입니다.
  const selectedRowRef = useRef<HTMLLIElement | null>(null);
  useEffect(() => {
    if (!selectedCode) return;
    selectedRowRef.current?.scrollIntoView({ block: 'nearest' });
  }, [selectedCode]);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center justify-between px-4 py-3 border-b border-[#eeeef2]">
        <h3 className="text-sm font-bold text-[#1a1b20]">
          {districtName.split(' ').pop()} 읍면동
        </h3>
        <BackButton label="시군구" onClick={onBack} />
      </div>
      {/* 동을 특정하지 못한 건을 숨기면 "이력 없음"이 거짓이 됩니다 — 없는 게
          아니라 어느 동인지 모르는 것이므로 반드시 함께 말합니다. */}
      {(info?.unlocated_count ?? 0) > 0 && (
        <p className="px-4 py-2 text-[11px] leading-relaxed text-[#a16207] bg-[#fefce8] border-b border-[#eeeef2]">
          주소가 불분명해 동을 특정하지 못한 침하 {info?.unlocated_count}건이 이
          목록 어디에도 들어 있지 않습니다.
        </p>
      )}
      <ul className="min-h-0 flex-1 overflow-y-auto divide-y divide-[#f4f4f7]">
        {rows.map((dong) => (
          <li
            key={dong.adm_code}
            ref={dong.adm_code === selectedCode ? selectedRowRef : undefined}
          >
            <button
              onClick={() => onFocus(dong)}
              aria-pressed={dong.adm_code === selectedCode}
              className={`w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors cursor-pointer ${
                dong.adm_code === selectedCode
                  ? 'bg-[#eef3fd]'
                  : 'hover:bg-[#f6f8fd]'
              }`}
            >
              <span className="flex-1 min-w-0 text-sm font-medium text-[#1a1b20] truncate">
                {dong.adm_name.split(' ').pop()}
              </span>
              {dong.subsidence_count > 0 ? (
                <span className="shrink-0 text-[11px] tabular-nums text-[#52514e]">
                  침하 {dong.subsidence_count}건
                  {dong.sewer_cause_count > 0 && (
                    <span className="text-[#d03b3b]">
                      {' '}
                      · 하수관 {dong.sewer_cause_count}
                    </span>
                  )}
                </span>
              ) : (
                <span className="shrink-0 text-[11px] text-[#c3c2b7]">확인된 이력 없음</span>
              )}
            </button>
          </li>
        ))}
        {rows.length === 0 && (
          <li className="px-4 py-8 text-center text-sm text-[#898781]">
            읍면동 정보가 없습니다.
          </li>
        )}
      </ul>
    </div>
  );
}

export function DetailPanel({
  detail,
  loading,
  onClose,
  dongTotal = 0,
  onShowDongs,
  history,
  rainMode = false,
  triggeredStats,
  triggeredRank,
}: {
  detail: PriorityScoreDetail | null;
  loading: boolean;
  onClose: () => void;
  dongTotal?: number;
  onShowDongs?: () => void;
  history?: PriorityHistoryResponse | null;
  /** 강수 반영 보기가 켜졌을 때만 강수 블록을 보여줍니다 — 평시 화면과 완전 분리. */
  rainMode?: boolean;
  /** 강수 반영 시의 요약 카드 값 — 목록 전체가 있어야 계산되므로 밖에서 받습니다. */
  triggeredStats?: {
    rank: number;
    total: number;
    sidoRank: number | null;
    sidoCount: number;
  } | null;
  /** 강수까지 반영했을 때의 전국 순위. 목록 전체가 있어야 계산되므로 밖에서 받습니다. */
  triggeredRank?: number | null;
}) {
  if (loading || !detail) {
    return (
      <div className="flex items-center justify-center h-64 text-sm text-[#737782]">
        산정 근거를 불러오는 중…
      </div>
    );
  }

  const shares = Object.entries(detail.contribution_shares).sort(
    (a, b) => b[1] - a[1],
  );
  const missing = new Set(detail.missing_features);
  const label = (key: string) => detail.display_names[key] ?? key;
  // 강수 모드의 지도 색과 같은 접기 — 배지만 평시 등급이면 화면이 모순됩니다.
  const detailRainGrade =
    rainMode && triggeredStats && triggeredStats.total > 0
      ? foldRankToGrade(triggeredStats.rank, triggeredStats.total)
      : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-start justify-between px-4 py-3 border-b border-[#eeeef2]">
        <h3 className="text-sm font-bold text-[#1a1b20]">{detail.full_name}</h3>
        <button
          onClick={onClose}
          className="p-1 text-[#737782] hover:text-[#1a1b20] cursor-pointer"
          aria-label="상세 닫기"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
      {/* 본문 스크롤 — 지표·이력이 길면 패널의 overflow-hidden 에 잘려
          아래 내용이 어떤 화면에서도 안 보였습니다. */}
      <div className="min-h-0 flex-1 overflow-y-auto">

      <div className="px-4 py-3 grid grid-cols-3 gap-2 border-b border-[#eeeef2]">
        {rainMode && triggeredStats ? (
          // 강수 모드에서는 요약 카드도 강수 반영 값으로 — 목록·지도만 바뀌고
          // 여기만 공식 값이면 서로 다른 순위가 나란히 보입니다. 파란 숫자가
          // "강수 반영 값" 표시입니다.
          <>
            <Stat label="전국 순위" value={`${triggeredStats.rank}위`} accent />
            <Stat
              label="시도 내"
              value={
                triggeredStats.sidoRank != null
                  ? `${triggeredStats.sidoRank}/${triggeredStats.sidoCount}위`
                  : '—'
              }
              accent
            />
            <Stat
              label="전국 상위"
              value={shareText((triggeredStats.rank / triggeredStats.total) * 100)}
              accent
            />
          </>
        ) : (
          <>
            <Stat label="전국 순위" value={`${detail.rank}위`} />
            <Stat
              label="시도 내"
              value={
                detail.sido_rank != null
                  ? `${detail.sido_rank}/${detail.sido_count}위`
                  : '—'
              }
            />
            <Stat label="전국 상위" value={topShare(detail.percentile)} />
          </>
        )}
      </div>

      {rainMode && (
        <RainTrigger
          features={detail.features}
          score={detail.score}
          rank={detail.rank}
          triggeredRank={triggeredRank}
        />
      )}

      {dongTotal > 0 && onShowDongs && (
        <div className="px-4 py-2.5 border-b border-[#eeeef2]">
          <button
            onClick={onShowDongs}
            className="w-full flex items-center justify-center gap-1.5 px-3 py-2 text-xs font-semibold text-[#0f458f] bg-[#eef3fd] hover:bg-[#dfe9fb] rounded-lg transition-colors cursor-pointer"
          >
            읍면동 {dongTotal}개 보기
          </button>
        </div>
      )}

      <div className="px-4 py-3">
        <div className="flex items-center justify-between mb-1">
          <h4 className="text-xs font-bold text-[#52514e]">
            이 구역이 위험한 이유
          </h4>
          <span
            className="text-[11px] font-semibold px-2 py-0.5 rounded-full text-white"
            style={{
              backgroundColor: GRADE_COLORS[detailRainGrade ?? detail.grade],
            }}
          >
            {detailRainGrade != null
              ? `${detailRainGrade}등급 ${GRADE_LABELS[detailRainGrade]}`
              : `${detail.grade}등급 ${detail.grade_label}`}
          </span>
        </div>
        <p className="text-[11px] text-[#898781] mb-2.5">
          {rainMode
            ? // 기여도 분해는 강수와 무관한 구조 요인이라 강수 모드에서도 그대로 —
              // 어느 점수의 분해인지만 못박습니다.
              `평시 위험도 ${detail.score.toFixed(3)} 기준`
            : `위험도 ${detail.score.toFixed(3)} 을 항목별로 쪼갠 비율`}
        </p>
        <ul className="space-y-2.5">
          {shares.map(([key, share]) => {
            const isMissing = missing.has(key);
            return (
              <li key={key}>
                <div className="flex items-baseline justify-between text-xs gap-2">
                  <span className="text-[#1a1b20]">
                    {label(key)}
                    {isMissing && (
                      <span className="ml-1.5 text-[10px] text-[#898781] border border-[#e1e0d9] rounded px-1 py-px">
                        자료 없음 · 중간값으로 대체
                      </span>
                    )}
                  </span>
                  <span className="tabular-nums text-[#52514e] font-medium shrink-0">
                    {(share * 100).toFixed(1)}%
                  </span>
                </div>
                {INDICATOR_HELP[key] && (
                  <p className="text-[10px] leading-snug text-[#898781] mt-0.5">
                    {INDICATOR_HELP[key]}
                  </p>
                )}
                <div className="mt-1 h-2 rounded-full bg-[#f0f0f4] overflow-hidden">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${Math.min(share * 100, 100)}%`,
                      backgroundColor: isMissing ? MISSING_BAR : '#2a78d6',
                    }}
                  />
                </div>
              </li>
            );
          })}
        </ul>

        {/* 산정 이력 — 입력이 달라진 산정마다 점이 하나 찍힙니다. 재산정을
            눌러도 입력이 그대로면 점은 늘지 않고 시각만 갱신됩니다. */}
        {history && history.points.length > 0 && (
          <div className="mt-3 pt-3 border-t border-[#eeeef2]">
            <h4 className="text-xs font-bold text-[#52514e]">
              순위 변화
            </h4>
            <p className="text-[11px] text-[#898781] mb-1.5">
              입력이 달라진 산정 {history.points.length}번의 전국 순위
            </p>
            {history.points.length === 1 ? (
              <p className="text-[11px] text-[#898781]">
                계산 1번 — 다시 계산할 때마다 점이 하나씩 추가
              </p>
            ) : (
              <HistorySparkline points={history.points} />
            )}
            {/* 실제 점검 결과. 점검 이력이 없을 때는 아무것도 띄우지 않습니다 —
                "없음"이라고 알리는 줄이 화면 대부분에서 소음이었습니다. */}
            {history.inspections.length > 0 && (
              <p className="mt-1.5 text-[11px] text-[#52514e]">
                실제 점검{' '}
                <span className="font-semibold tabular-nums">
                  {history.inspections.length}회
                </span>{' '}
                · 발견한 결함{' '}
                <span className="font-semibold tabular-nums">
                  {history.inspections.reduce(
                    (sum, item) => sum + item.defect_count,
                    0,
                  )}
                  건
                </span>
              </p>
            )}
          </div>
        )}
      </div>
      </div>
    </div>
  );
}

// 위험도 원값(0~1.5)은 절대적인 뜻이 없어 읽히지 않습니다. 백분위를 "상위 몇 %"로
// 바꿔 보여줍니다 — 1% 미만은 소수 한 자리까지 살립니다.
function shareText(share: number): string {
  const clamped = Math.max(share, 0);
  return clamped < 1 ? `${clamped.toFixed(1)}%` : `${Math.round(clamped)}%`;
}

function topShare(percentile: number): string {
  return shareText((1 - percentile) * 100);
}

function shortDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return `${date.getMonth() + 1}/${date.getDate()}`;
}

function HistorySparkline({
  points,
}: {
  points: PriorityHistoryResponse['points'];
}) {
  // 시간 오름차순, y 는 순위(1위가 위).
  const ordered = [...points].reverse();
  const width = 320;
  const height = 56;
  const pad = 8;
  const ranks = ordered.map((point) => point.rank);
  const minRank = Math.min(...ranks);
  const maxRank = Math.max(...ranks);
  const span = Math.max(maxRank - minRank, 1);
  const x = (index: number) =>
    pad + (index / Math.max(ordered.length - 1, 1)) * (width - pad * 2);
  const y = (rank: number) => pad + ((rank - minRank) / span) * (height - pad * 2);
  const path = ordered
    .map((point, index) => `${index ? 'L' : 'M'}${x(index).toFixed(1)},${y(point.rank).toFixed(1)}`)
    .join('');
  const latest = ordered[ordered.length - 1];
  const previous = ordered[ordered.length - 2];
  const delta = previous.rank - latest.rank; // 양수 = 순위 상승(더 위험해짐)

  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-14">
        <path d={path} fill="none" stroke="#2a78d6" strokeWidth={2} />
        {ordered.map((point, index) => (
          <circle
            key={point.batch_id + String(index)}
            cx={x(index)}
            cy={y(point.rank)}
            r={3}
            fill="#2a78d6"
            stroke="#fcfcfb"
            strokeWidth={1.5}
          />
        ))}
      </svg>
      {/* 점만 찍혀 있으면 가로축이 뭔지 알 수 없어서 양 끝 날짜를 적습니다. */}
      <div className="flex justify-between text-[10px] text-[#898781] tabular-nums -mt-1">
        <span>{shortDate(ordered[0].calculated_at)}</span>
        <span>{shortDate(latest.calculated_at)}</span>
      </div>
      <p className="mt-1 text-[11px] text-[#52514e] tabular-nums">
        전국 {previous.rank}위 →{' '}
        <span className="font-semibold">{latest.rank}위</span>
      </p>
    </div>
  );
}

// 최근 강수. 순위에 안 들어가는 값이라 설명 문장을 길게 붙였더니 오히려 읽히지
// 않았습니다. 문장을 지우고 **백분위 한 마디**로 뜻이 통하게 바꿨습니다.
function RainTrigger({
  features,
  score,
  rank,
  triggeredRank,
}: {
  features: Record<string, number | null>;
  score: number;
  rank: number;
  triggeredRank?: number | null;
}) {
  const rainfall = features.rainfall_30d_mm;
  const nationalMean = features.rainfall_national_mean_mm;
  const triggered = features.triggered_score;

  if (rainfall == null) return null;

  // 강수를 반영해도 점수가 사실상 그대로면(비가 적은 구역) 한 줄을 더 쓸 이유가
  // 없습니다. 0.001 미만 차이는 표시 자리수에서 구분되지 않습니다.
  const shift = triggered != null ? triggered - score : 0;
  const showTriggered = triggered != null && shift >= 0.001;
  const rankGain = triggeredRank != null ? rank - triggeredRank : null;

  return (
    <div className="px-4 py-2.5 border-b border-[#eeeef2] space-y-1">
      <div className="flex items-center gap-1.5 min-w-0">
        <CloudRain className="w-3.5 h-3.5 text-[#737782] shrink-0" />
        <span className="text-[11px] text-[#737782] truncate">
          최근 30일 비 <span className="tabular-nums">{rainfall.toFixed(0)}</span>mm
          {nationalMean != null && (
            <span className="text-[#a5a3a0]">
              {' '}
              · 전국 평균 <span className="tabular-nums">{nationalMean.toFixed(0)}</span>mm
            </span>
          )}
        </span>
      </div>
      {/* 강수는 순위·등급에 들어가지 않습니다. 이 줄은 "이번 달만 보면" 이라는
          참고값입니다 — 문구 대신 툴팁으로만 설명합니다 (2026-08-05 결정). */}
      {showTriggered && (
        <p
          className="pl-5 text-[11px] text-[#737782]"
          title="강수는 시점에 따라 변하므로 순위·등급에는 반영하지 않습니다. 이번 달 투입 순서를 조정할 때만 참고하세요."
        >
          강수 반영 시{' '}
          <span className="tabular-nums text-[#52514e]">{score.toFixed(3)}</span> →{' '}
          <span className="tabular-nums font-semibold text-[#0f458f]">
            {triggered.toFixed(3)}
          </span>
          {rankGain != null && rankGain > 0 && (
            <>
              {' '}
              <span className="tabular-nums">
                ({rank}위 → {triggeredRank}위)
              </span>
            </>
          )}
        </p>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  accent = false,
}: {
  label: string;
  value: string;
  /** 강수 반영 값 표시 — 공식 값과 구분되게 파란 숫자로. */
  accent?: boolean;
}) {
  return (
    <div className="bg-[#f8f9fc] rounded-lg px-2.5 py-2 text-center">
      <p className="text-[10px] text-[#898781]">{label}</p>
      <p
        className={`text-sm font-bold tabular-nums mt-0.5 ${
          accent ? 'text-[#0f458f]' : 'text-[#1a1b20]'
        }`}
      >
        {value}
      </p>
    </div>
  );
}
