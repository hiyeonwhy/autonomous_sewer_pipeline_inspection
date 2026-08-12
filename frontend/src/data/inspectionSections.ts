/**
 * 광주광역시 읍면동별 점검 구간 수.
 *
 * 동 경계 면적(`backend/priority_engine/data/admin_dongs.geojson`)을 4분위로 나눠
 * 구간을 3~6개 배정했다. 넓은 동일수록 점검할 관로가 길다는 전제다.
 * 면적 경계(km²): 0.75, 1.46, 4.05
 *
 * 생성 스크립트가 굽은 결과이므로 손으로 고치지 않는다 — 동 경계 데이터가 바뀌면
 * 다시 굽는다. 런타임에 6.2MB geojson 을 파싱할 이유가 없고 구간 수는 고정값이다.
 *
 * 지금은 광주만 있다. 다른 시도는 경계 데이터에서 같은 방식으로 추가한다.
 */
export interface DongSectionInfo {
  admCode: string;
  /** 「광주광역시 광산구 하남동」처럼 시도까지 붙은 전체 이름. */
  name: string;
  sectionCount: number;
}

export const GWANGJU_DONG_SECTIONS: DongSectionInfo[] = [
  { admCode: "2911052500", name: "광주광역시 동구 충장동", sectionCount: 4 },
  { admCode: "2911054500", name: "광주광역시 동구 동명동", sectionCount: 3 },
  { admCode: "2911056000", name: "광주광역시 동구 계림1동", sectionCount: 3 },
  { admCode: "2911057000", name: "광주광역시 동구 계림2동", sectionCount: 3 },
  { admCode: "2911059000", name: "광주광역시 동구 산수1동", sectionCount: 3 },
  { admCode: "2911060000", name: "광주광역시 동구 산수2동", sectionCount: 4 },
  { admCode: "2911062000", name: "광주광역시 동구 지산1동", sectionCount: 3 },
  { admCode: "2911063000", name: "광주광역시 동구 지산2동", sectionCount: 5 },
  { admCode: "2911065500", name: "광주광역시 동구 서남동", sectionCount: 5 },
  { admCode: "2911068500", name: "광주광역시 동구 학동", sectionCount: 3 },
  { admCode: "2911071000", name: "광주광역시 동구 학운동", sectionCount: 6 },
  { admCode: "2911073000", name: "광주광역시 동구 지원1동", sectionCount: 4 },
  { admCode: "2911074000", name: "광주광역시 동구 지원2동", sectionCount: 6 },
  { admCode: "2914057500", name: "광주광역시 서구 양동", sectionCount: 3 },
  { admCode: "2914059000", name: "광주광역시 서구 양3동", sectionCount: 3 },
  { admCode: "2914065000", name: "광주광역시 서구 농성1동", sectionCount: 4 },
  { admCode: "2914066000", name: "광주광역시 서구 농성2동", sectionCount: 3 },
  { admCode: "2914073000", name: "광주광역시 서구 광천동", sectionCount: 4 },
  { admCode: "2914074000", name: "광주광역시 서구 유덕동", sectionCount: 6 },
  { admCode: "2914074500", name: "광주광역시 서구 치평동", sectionCount: 5 },
  { admCode: "2914075100", name: "광주광역시 서구 상무1동", sectionCount: 5 },
  { admCode: "2914075200", name: "광주광역시 서구 상무2동", sectionCount: 5 },
  { admCode: "2914076000", name: "광주광역시 서구 화정1동", sectionCount: 4 },
  { admCode: "2914077000", name: "광주광역시 서구 화정2동", sectionCount: 4 },
  { admCode: "2914078000", name: "광주광역시 서구 화정3동", sectionCount: 3 },
  { admCode: "2914079000", name: "광주광역시 서구 화정4동", sectionCount: 4 },
  { admCode: "2914080000", name: "광주광역시 서구 서창동", sectionCount: 6 },
  { admCode: "2914082100", name: "광주광역시 서구 금호1동", sectionCount: 5 },
  { admCode: "2914082400", name: "광주광역시 서구 금호2동", sectionCount: 4 },
  { admCode: "2914083000", name: "광주광역시 서구 풍암동", sectionCount: 6 },
  { admCode: "2914084000", name: "광주광역시 서구 동천동", sectionCount: 4 },
  { admCode: "2915551000", name: "광주광역시 남구 양림동", sectionCount: 3 },
  { admCode: "2915552000", name: "광주광역시 남구 방림1동", sectionCount: 3 },
  { admCode: "2915553000", name: "광주광역시 남구 방림2동", sectionCount: 4 },
  { admCode: "2915553700", name: "광주광역시 남구 봉선1동", sectionCount: 3 },
  { admCode: "2915553800", name: "광주광역시 남구 봉선2동", sectionCount: 5 },
  { admCode: "2915554500", name: "광주광역시 남구 사직동", sectionCount: 3 },
  { admCode: "2915560500", name: "광주광역시 남구 월산동", sectionCount: 4 },
  { admCode: "2915563000", name: "광주광역시 남구 월산4동", sectionCount: 3 },
  { admCode: "2915564000", name: "광주광역시 남구 월산5동", sectionCount: 3 },
  { admCode: "2915567000", name: "광주광역시 남구 백운1동", sectionCount: 3 },
  { admCode: "2915568000", name: "광주광역시 남구 백운2동", sectionCount: 3 },
  { admCode: "2915569000", name: "광주광역시 남구 주월1동", sectionCount: 5 },
  { admCode: "2915570000", name: "광주광역시 남구 주월2동", sectionCount: 4 },
  { admCode: "2915570500", name: "광주광역시 남구 진월동", sectionCount: 5 },
  { admCode: "2915571000", name: "광주광역시 남구 효덕동", sectionCount: 6 },
  { admCode: "2915572000", name: "광주광역시 남구 송암동", sectionCount: 6 },
  { admCode: "2915573000", name: "광주광역시 남구 대촌동", sectionCount: 6 },
  { admCode: "2917051000", name: "광주광역시 북구 중흥1동", sectionCount: 3 },
  { admCode: "2917053500", name: "광주광역시 북구 중흥동", sectionCount: 4 },
  { admCode: "2917055500", name: "광주광역시 북구 중앙동", sectionCount: 3 },
  { admCode: "2917057000", name: "광주광역시 북구 임동", sectionCount: 4 },
  { admCode: "2917058000", name: "광주광역시 북구 신안동", sectionCount: 4 },
  { admCode: "2917059000", name: "광주광역시 북구 용봉동", sectionCount: 5 },
  { admCode: "2917060100", name: "광주광역시 북구 운암1동", sectionCount: 4 },
  { admCode: "2917060200", name: "광주광역시 북구 운암2동", sectionCount: 4 },
  { admCode: "2917060300", name: "광주광역시 북구 운암3동", sectionCount: 4 },
  { admCode: "2917061500", name: "광주광역시 북구 동림동", sectionCount: 5 },
  { admCode: "2917062000", name: "광주광역시 북구 우산동", sectionCount: 4 },
  { admCode: "2917063500", name: "광주광역시 북구 풍향동", sectionCount: 3 },
  { admCode: "2917065000", name: "광주광역시 북구 문화동", sectionCount: 5 },
  { admCode: "2917065600", name: "광주광역시 북구 문흥1동", sectionCount: 5 },
  { admCode: "2917065700", name: "광주광역시 북구 문흥2동", sectionCount: 3 },
  { admCode: "2917066100", name: "광주광역시 북구 두암1동", sectionCount: 3 },
  { admCode: "2917066200", name: "광주광역시 북구 두암2동", sectionCount: 5 },
  { admCode: "2917066300", name: "광주광역시 북구 두암3동", sectionCount: 4 },
  { admCode: "2917066600", name: "광주광역시 북구 삼각동", sectionCount: 5 },
  { admCode: "2917066900", name: "광주광역시 북구 일곡동", sectionCount: 5 },
  { admCode: "2917067300", name: "광주광역시 북구 매곡동", sectionCount: 5 },
  { admCode: "2917067600", name: "광주광역시 북구 오치1동", sectionCount: 5 },
  { admCode: "2917067700", name: "광주광역시 북구 오치2동", sectionCount: 3 },
  { admCode: "2917068500", name: "광주광역시 북구 석곡동", sectionCount: 6 },
  { admCode: "2917069500", name: "광주광역시 북구 건국동", sectionCount: 6 },
  { admCode: "2917069600", name: "광주광역시 북구 양산동", sectionCount: 6 },
  { admCode: "2917069700", name: "광주광역시 북구 신용동", sectionCount: 5 },
  { admCode: "2920051500", name: "광주광역시 광산구 송정1동", sectionCount: 4 },
  { admCode: "2920052500", name: "광주광역시 광산구 송정2동", sectionCount: 4 },
  { admCode: "2920054000", name: "광주광역시 광산구 도산동", sectionCount: 5 },
  { admCode: "2920055000", name: "광주광역시 광산구 신흥동", sectionCount: 6 },
  { admCode: "2920056500", name: "광주광역시 광산구 어룡동", sectionCount: 6 },
  { admCode: "2920058000", name: "광주광역시 광산구 우산동", sectionCount: 6 },
  { admCode: "2920060000", name: "광주광역시 광산구 월곡1동", sectionCount: 3 },
  { admCode: "2920061000", name: "광주광역시 광산구 월곡2동", sectionCount: 4 },
  { admCode: "2920062000", name: "광주광역시 광산구 비아동", sectionCount: 6 },
  { admCode: "2920062400", name: "광주광역시 광산구 첨단1동", sectionCount: 5 },
  { admCode: "2920062600", name: "광주광역시 광산구 첨단2동", sectionCount: 5 },
  { admCode: "2920063000", name: "광주광역시 광산구 신가동", sectionCount: 5 },
  { admCode: "2920063500", name: "광주광역시 광산구 운남동", sectionCount: 5 },
  { admCode: "2920063700", name: "광주광역시 광산구 수완동", sectionCount: 6 },
  { admCode: "2920064000", name: "광주광역시 광산구 하남동", sectionCount: 6 },
  { admCode: "2920065000", name: "광주광역시 광산구 임곡동", sectionCount: 6 },
  { admCode: "2920066000", name: "광주광역시 광산구 동곡동", sectionCount: 6 },
  { admCode: "2920067000", name: "광주광역시 광산구 평동", sectionCount: 6 },
  { admCode: "2920068000", name: "광주광역시 광산구 삼도동", sectionCount: 6 },
  { admCode: "2920069000", name: "광주광역시 광산구 본량동", sectionCount: 6 },
  { admCode: "2920070000", name: "광주광역시 광산구 신창동", sectionCount: 6 },
];

/** 동 이름으로 구간 수를 찾는다. 표에 없으면 최소값을 쓴다. */
export function sectionCountFor(dongName: string): number {
  const found = GWANGJU_DONG_SECTIONS.find((dong) => dong.name === dongName);
  return found ? found.sectionCount : 3;
}

/** 「... 하남동 구간1」 형태의 선택 항목을 만든다. */
export function sectionOptionsFor(dongName: string): string[] {
  if (!dongName.trim()) return [];
  return Array.from(
    { length: sectionCountFor(dongName) },
    (_unused, index) => `${dongName} 구간${index + 1}`,
  );
}
