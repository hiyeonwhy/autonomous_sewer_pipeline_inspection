import { useState, useEffect } from 'react';
import { Search, Plus, Download, AlertTriangle, FileText, LayoutGrid, Calendar, LogOut, Eye, CheckCircle2, Power, ChevronLeft, ChevronRight } from 'lucide-react';
import { InspectionItem, ReportMeta } from '../types';
import { API_BASE_URL } from '../api/client';
import { PLACEHOLDER_IMAGE } from '../api/media';

const REPAIR_STATUSES = ['보수 이전', '보수 중', '보수완료'] as const;
const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'];

function getDateKey(year: number, month: number, day: number) {
  return `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

function getCalendarMonth(date: string) {
  const parsed = date ? new Date(`${date}T00:00:00`) : new Date();
  return new Date(parsed.getFullYear(), parsed.getMonth(), 1);
}

/** 서버가 정한 파일명(보고서 번호)을 그대로 쓴다. 없으면 호출부에서 대체한다. */
function readPdfFilename(response: Response): string | null {
  const disposition = response.headers.get('Content-Disposition');
  const matched = disposition?.match(/filename="([^"]+)"/);
  return matched ? matched[1] : null;
}

/** 실패 응답은 FastAPI의 {detail: "..."} 이거나 그냥 텍스트다. */
async function readPdfErrorMessage(response: Response): Promise<string> {
  const fallback = `서버 응답 ${response.status}`;
  try {
    const text = await response.text();
    if (!text) return fallback;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (typeof parsed.detail === 'string') return parsed.detail;
    } catch {
      // JSON이 아니면 본문을 그대로 보여준다.
    }
    return text.slice(0, 300);
  } catch {
    return fallback;
  }
}

function getRepairStatus(status?: string): typeof REPAIR_STATUSES[number] {
  return REPAIR_STATUSES.includes(status as typeof REPAIR_STATUSES[number])
    ? status as typeof REPAIR_STATUSES[number]
    : '보수 이전';
}

function getNextRepairStatus(status?: string): typeof REPAIR_STATUSES[number] {
  const currentIndex = REPAIR_STATUSES.indexOf(getRepairStatus(status));
  return REPAIR_STATUSES[(currentIndex + 1) % REPAIR_STATUSES.length];
}

function getRepairButtonLabel(status?: string): '보수 요청' | '보수 완료' | '보수 보류' {
  switch (getRepairStatus(status)) {
    case '보수 중':
      return '보수 완료';
    case '보수완료':
      return '보수 보류';
    case '보수 이전':
    default:
      return '보수 요청';
  }
}

interface InspectionReportProps {
  reports: ReportMeta[];
  inspections: InspectionItem[];
  onUpdateInspection: (id: string, changes: Partial<InspectionItem>) => void;
  selectedDate: string;
  setSelectedDate: (date: string) => void;
  selectedReportId: string;
  setSelectedReportId: (id: string) => void;
  onStartNewInspection: () => void;
}

export default function InspectionReport({
  reports,
  inspections,
  onUpdateInspection,
  selectedDate,
  setSelectedDate,
  selectedReportId,
  setSelectedReportId,
  onStartNewInspection,
}: InspectionReportProps) {
  
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [isCalendarOpen, setIsCalendarOpen] = useState<boolean>(false);
  const [calendarMonth, setCalendarMonth] = useState<Date>(() => getCalendarMonth(selectedDate));
  const [isDownloadingPdf, setIsDownloadingPdf] = useState<boolean>(false);
  const threeDAnalysisUrl = import.meta.env.VITE_3D_ANALYSIS_URL?.trim() ?? '';

  const openThreeDAnalysis = (item: InspectionItem) => {
    if (!threeDAnalysisUrl) return;
    const target = new URL(threeDAnalysisUrl);
    target.searchParams.set('defect_id', item.id);
    target.searchParams.set('report_id', item.reportId);
    window.location.assign(target.toString());
  };

  // Compute global metrics as fallback or overall dashboard totals
  const globalTotalInspected = inspections.length;
  const globalCriticalCount = inspections.filter(ins => ins.severity === 'critical').length;

  // Active Report Metadata
  const reportsForDate = selectedDate === ''
    ? reports
    : reports.filter((r) => r.date === selectedDate);
  const reportDates = new Set(reports.map((report) => report.date));
  const calendarYear = calendarMonth.getFullYear();
  const calendarMonthIndex = calendarMonth.getMonth();
  const firstWeekday = new Date(calendarYear, calendarMonthIndex, 1).getDay();
  const daysInMonth = new Date(calendarYear, calendarMonthIndex + 1, 0).getDate();
  const calendarDays = [
    ...Array.from({ length: firstWeekday }, () => null),
    ...Array.from({ length: daysInMonth }, (_, index) => index + 1),
  ];

  const activeReport = selectedReportId !== '' ? (reports.find((r) => r.id === selectedReportId) || null) : null;

  // Filter inspections matching active report ID or show empty if "선택" is active
  const filteredInspections = selectedReportId === ''
    ? []
    : inspections.filter((ins) => ins.reportId === selectedReportId);

  const dateInspections = selectedDate === ''
    ? inspections
    : inspections.filter(ins => ins.date === selectedDate);

  const totalInspectedVal = selectedReportId !== ''
    ? filteredInspections.length
    : (selectedDate !== '' ? dateInspections.length : globalTotalInspected);

  const criticalCountVal = selectedReportId !== ''
    ? filteredInspections.filter(ins => ins.severity === 'critical').length
    : (selectedDate !== '' ? dateInspections.filter(ins => ins.severity === 'critical').length : globalCriticalCount);

  // Synchronize Report ID selection when the date changes - default to "선택" (empty string) so user selects manually
  useEffect(() => {
    setSelectedReportId('');
  }, [selectedDate]);

  useEffect(() => {
    if (selectedDate) {
      setCalendarMonth(getCalendarMonth(selectedDate));
    }
  }, [selectedDate]);

  // Dynamic client-side pagination variables
  const ITEMS_PER_PAGE = 3;
  const totalItems = filteredInspections.length;
  const totalPages = Math.max(1, Math.ceil(totalItems / ITEMS_PER_PAGE));

  // Reset currentPage to 1 if we change reports or dates
  useEffect(() => {
    setCurrentPage(1);
  }, [selectedReportId, selectedDate]);

  const startIndex = totalItems === 0 ? 0 : (currentPage - 1) * ITEMS_PER_PAGE + 1;
  const endIndex = Math.min(totalItems, currentPage * ITEMS_PER_PAGE);
  const currentItems = filteredInspections.slice((currentPage - 1) * ITEMS_PER_PAGE, currentPage * ITEMS_PER_PAGE);

  // 보고서 PDF는 백엔드가 만든다(GET /api/reports/{id}/document.pdf).
  // Content-Disposition: attachment 라서 링크를 클릭하면 바로 내려받아진다.
  const downloadReportPdf = async () => {
    if (!activeReport || isDownloadingPdf) {
      if (!activeReport) alert('출력할 보고서를 좌측 필터에서 먼저 선택해 주세요.');
      return;
    }
    // Mock 모드의 보고서 번호(REP-...)는 서버에 없는 값이라 받을 수 없다.
    if (!/^\d+$/.test(activeReport.id)) {
      alert('서버에 저장된 보고서만 내려받을 수 있습니다.');
      return;
    }

    // 링크를 그냥 클릭시키면 서버가 오류를 내는 순간 브라우저가 화면을 떠나
    // 원시 JSON을 띄운다(점검 화면 상태가 날아감). blob으로 받아서 실패를
    // 이 화면 안에서 처리한다.
    setIsDownloadingPdf(true);
    try {
      const response = await fetch(`${API_BASE_URL}/reports/${activeReport.id}/document.pdf`);
      if (!response.ok) {
        throw new Error(await readPdfErrorMessage(response));
      }

      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const link = window.document.createElement('a');
      link.href = objectUrl;
      link.download = readPdfFilename(response) ?? `report-${activeReport.id}.pdf`;
      window.document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (error) {
      alert(`보고서 PDF를 내려받지 못했습니다.\n${error instanceof Error ? error.message : error}`);
    } finally {
      setIsDownloadingPdf(false);
    }
  };

  // 1. Rename to "시스템 종료" instead of "시스템 로그아웃"
  const handleSystemShutdown = () => {
    if (confirm('스마트 하수관로 관리 시스템을 완전히 종료하시겠습니까?')) {
      alert('시스템이 안전하게 종료되었습니다. 모든 실시간 로봇 인터페이스 및 CCTV 스트림이 오프라인 상태로 차단됩니다.');
      window.location.reload();
    }
  };

  return (
    <div className="flex flex-col md:flex-row h-[calc(100vh-72px)] overflow-hidden">
      
      {/* LEFT SIDEBAR: 점검 정보 (Inspection Sidebar) */}
      <aside className="w-full md:w-[330px] bg-[#ededf4] border-r border-[#c3c6d3] flex flex-col justify-between shrink-0">
        <div className="p-5 flex-1 overflow-y-auto space-y-5">
          <div className="flex items-center space-x-2 pb-4 border-b border-[#c3c6d3]">
            <Calendar className="w-5 h-5 text-[#0f458f]" />
            <h2 className="text-[15px] font-bold text-[#1a1b20]">점검 정보 필터</h2>
          </div>

          {/* Date Picker */}
          <div className="space-y-1.5">
            <label className="text-xs font-bold text-[#434751] block">날짜 선택</label>
            <div className="relative">
              <button
                type="button"
                onClick={() => setIsCalendarOpen((open) => !open)}
                className="flex h-10 w-full items-center justify-between rounded-lg border border-[#c3c6d3] bg-white px-3 text-xs font-semibold text-[#1a1b20] transition-colors hover:border-[#0f458f] focus:border-[#0f458f] focus:outline-none"
                aria-expanded={isCalendarOpen}
                aria-label="점검 보고서 날짜 선택"
              >
                <span>{selectedDate || '날짜를 선택하세요'}</span>
                <Calendar className="h-4 w-4 text-[#0f458f]" />
              </button>

              {isCalendarOpen && (
                <div className="absolute left-0 top-11 z-40 w-full min-w-[280px] rounded-xl border border-[#c3c6d3] bg-white p-3 shadow-xl">
                  <div className="mb-3 flex items-center justify-between">
                    <button
                      type="button"
                      onClick={() => setCalendarMonth(new Date(calendarYear, calendarMonthIndex - 1, 1))}
                      className="rounded-md p-1.5 text-[#434751] hover:bg-[#f3f3fa]"
                      aria-label="이전 달"
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </button>
                    <strong className="text-sm text-[#1a1b20]">
                      {calendarYear}년 {calendarMonthIndex + 1}월
                    </strong>
                    <button
                      type="button"
                      onClick={() => setCalendarMonth(new Date(calendarYear, calendarMonthIndex + 1, 1))}
                      className="rounded-md p-1.5 text-[#434751] hover:bg-[#f3f3fa]"
                      aria-label="다음 달"
                    >
                      <ChevronRight className="h-4 w-4" />
                    </button>
                  </div>

                  <div className="grid grid-cols-7 text-center">
                    {WEEKDAYS.map((weekday, index) => (
                      <span
                        key={weekday}
                        className={`pb-2 text-[10px] font-bold ${
                          index === 0 ? 'text-red-500' : index === 6 ? 'text-blue-500' : 'text-[#737782]'
                        }`}
                      >
                        {weekday}
                      </span>
                    ))}

                    {calendarDays.map((day, index) => {
                      if (day === null) {
                        return <span key={`empty-${index}`} className="h-8" />;
                      }

                      const dateKey = getDateKey(calendarYear, calendarMonthIndex, day);
                      const hasReport = reportDates.has(dateKey);
                      const isSelected = selectedDate === dateKey;

                      return (
                        <button
                          key={dateKey}
                          type="button"
                          onClick={() => {
                            setSelectedDate(dateKey);
                            setIsCalendarOpen(false);
                          }}
                          className={`mx-auto flex h-8 w-8 items-center justify-center rounded-md text-xs font-semibold transition-colors ${
                            hasReport
                              ? 'bg-sky-100 text-[#0f458f] hover:bg-sky-200'
                              : 'text-[#434751] hover:bg-[#f3f3fa]'
                          } ${isSelected ? 'ring-2 ring-[#0f458f] ring-offset-1' : ''}`}
                          title={hasReport ? '점검 보고서 있음' : undefined}
                        >
                          {day}
                        </button>
                      );
                    })}
                  </div>

                  <div className="mt-3 flex items-center justify-between border-t border-[#ededf4] pt-2">
                    <span className="flex items-center gap-1.5 text-[10px] font-semibold text-[#737782]">
                      <span className="h-3 w-3 rounded-sm bg-sky-100" />
                      보고서가 있는 날짜
                    </span>
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedDate('');
                        setIsCalendarOpen(false);
                      }}
                      className="text-[10px] font-bold text-[#0f458f] hover:underline"
                    >
                      전체 날짜
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Interactive Clickable Reports List based on filtered dates */}
          <div className="space-y-1.5">
            <label className="text-xs font-bold text-[#434751] block">📋 보고서 목록 ({reportsForDate.length}건)</label>
            
            {reportsForDate.length === 0 ? (
              <div className="p-4 bg-white/50 rounded-lg text-center text-xs text-[#737782] border border-dashed border-[#c3c6d3]">
                등록된 보고서가 없습니다.
              </div>
            ) : (
              <div className="relative">
                <select
                  value={selectedReportId}
                  onChange={(e) => setSelectedReportId(e.target.value)}
                  className="w-full h-11 pl-3.5 pr-10 bg-white border border-[#c3c6d3] rounded-lg text-xs font-extrabold text-[#1a1b20] focus:border-[#0f458f] focus:ring-1 focus:ring-[#0f458f] focus:outline-none cursor-pointer appearance-none shadow-2xs"
                >
                  <option value="" className="font-sans py-2 text-[#737782] font-semibold">선택</option>
                  {reportsForDate.map((rep) => (
                    <option key={rep.id} value={rep.id} className="font-sans py-2 text-[#1a1b20]">
                      {rep.id} ({rep.region})
                    </option>
                  ))}
                </select>
                {/* Custom select chevron decoration */}
                <div className="absolute inset-y-0 right-3 flex items-center pointer-events-none text-[#737782]">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M19 9l-7 7-7-7" />
                  </svg>
                </div>
              </div>
            )}
          </div>

          {/* Readonly Metadata Section */}
          <div className="bg-white rounded-lg border border-[#c3c6d3] overflow-hidden shadow-2xs p-3.5 space-y-3">
            <p className="text-[11px] text-[#737782] font-bold uppercase tracking-wider border-b border-[#ededf4] pb-1.5">선택 보고서 상세</p>
            {selectedReportId === '' ? (
              <div className="py-6 text-center text-xs font-extrabold text-[#ba1a1a]">
                보고서를 선택하세요
              </div>
            ) : activeReport ? (
              <div className="space-y-2 text-xs">
                <div className="flex justify-between items-start gap-2">
                  <span className="text-[#737782] font-medium shrink-0">점검 지역</span>
                  <span className="text-[#1a1b20] font-bold text-right leading-tight max-w-[180px] break-keep">{activeReport.region}</span>
                </div>
                <div className="flex justify-between items-start gap-2">
                  <span className="text-[#737782] font-medium shrink-0">로봇 종류</span>
                  <span className="text-[#1a1b20] font-bold text-right leading-tight max-w-[180px] break-keep">
                    {activeReport.robotName ?? '정보 없음'}
                  </span>
                </div>
                <div className="flex justify-between items-start gap-2">
                  <span className="text-[#737782] font-medium shrink-0">승인 운영자</span>
                  <span className="text-[#1a1b20] font-bold text-right leading-tight max-w-[180px] break-keep">
                    {activeReport.operatorName ?? '정보 없음'}
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-[#737782] font-medium">점검 일자</span>
                  <span className="text-[#1a1b20] font-mono font-semibold">{activeReport.date}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-[#737782] font-medium">주행 시간</span>
                  <span className="text-[#1a1b20] font-mono font-semibold">{activeReport.duration}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-[#737782] font-medium">보고서 번호</span>
                  <span className="text-[#0f458f] font-mono font-bold">{activeReport.id}</span>
                </div>
              </div>
            ) : (
              <div className="py-4 text-center text-xs font-semibold text-[#737782]">
                선택된 보고서 정보가 없습니다.
              </div>
            )}
          </div>
        </div>

        {/* 1. 시스템 로그아웃 -> 시스템 종료 버튼 */}
        <div className="p-4 border-t border-[#c3c6d3] bg-[#f3f3fa]">
          <button
            onClick={handleSystemShutdown}
            className="w-full py-2.5 bg-[#ba1a1a] hover:bg-[#a21212] text-white rounded-lg text-xs font-bold transition-all flex items-center justify-center space-x-1.5 shadow-sm cursor-pointer border border-[#8f1010]"
          >
            <Power className="w-3.5 h-3.5" />
            <span>시스템 종료</span>
          </button>
        </div>
      </aside>

      {/* RIGHT MAIN PANEL: Dashboard report content */}
      <main className="flex-1 bg-[#f9f9ff] p-6 overflow-y-auto space-y-6">
        
        {/* Top bar with breadcrumb and primary actions */}
        <div className="flex flex-col sm:flex-row justify-between sm:items-center gap-4">
          <div>
            <nav className="text-xs text-[#737782] font-medium mb-1 flex items-center space-x-1">
              <span>제어 센터</span>
              <span>&gt;</span>
              <span className="text-[#0f458f]">아카이브</span>
            </nav>
            <h2 className="text-2xl font-black text-[#1a1b20] tracking-tight">
              하수관로 정밀 점검 보고서
            </h2>
          </div>

          {/* 보고서 출력 and "새로운 점검" buttons */}
          <div className="flex space-x-2 shrink-0">
            <button
              onClick={downloadReportPdf}
              disabled={isDownloadingPdf}
              title="선택한 보고서를 조사 보고서 양식 PDF로 내려받습니다. 종합 평가를 아직 만들지 않은 보고서는 첫 내려받기에서 생성하므로 시간이 더 걸립니다."
              className="flex items-center space-x-1.5 px-4 h-10 bg-white hover:bg-[#f3f3fa] border border-[#c3c6d3] text-xs font-bold text-[#434751] rounded-lg transition-colors cursor-pointer disabled:opacity-60 disabled:cursor-wait"
            >
              <Download className="w-4 h-4 text-[#737782]" />
              <span>{isDownloadingPdf ? 'PDF 생성 중…' : 'PDF 내려받기'}</span>
            </button>
            
            {/* 3. "새로운 점검" button: triggers navigation to real-time monitor page & starts new prep */}
            <button
              onClick={onStartNewInspection}
              className="flex items-center space-x-1.5 px-4 h-10 bg-[#0f458f] hover:bg-[#0a336c] text-xs font-bold text-white rounded-lg transition-all shadow-sm cursor-pointer hover:shadow-md hover:scale-[1.01]"
            >
              <Plus className="w-4 h-4 stroke-[3]" />
              <span>새로운 점검</span>
            </button>
          </div>
        </div>

        {/* METRICS ROW (2 Cards) - ALWAYS rendered at the top of the main panel */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          {/* Total Inspection Records */}
          <div className="bg-white rounded-xl border border-[#c3c6d3] p-5 flex items-center justify-between shadow-xs relative overflow-hidden group">
            <div className="space-y-1">
              <p className="text-xs font-bold text-[#737782] uppercase tracking-wider">전체 기록</p>
              <p className="text-[26px] font-black text-[#1a1b20]">
                {totalInspectedVal.toLocaleString()}{' '}
                <span className="text-xs font-semibold text-[#737782] font-sans">건 발견</span>
              </p>
            </div>
            <div className="p-3 bg-blue-50 text-[#0f458f] rounded-lg group-hover:scale-110 transition-transform">
              <LayoutGrid className="w-6 h-6" />
            </div>
            <div className="absolute bottom-0 left-0 right-0 h-1 bg-[#0f458f]" />
          </div>

          {/* Critical Defects (1등급) */}
          <div className="bg-white rounded-xl border border-[#c3c6d3] p-5 flex items-center justify-between shadow-xs relative overflow-hidden group">
            <div className="space-y-1">
              <p className="text-xs font-bold text-[#737782] uppercase tracking-wider">주요 결함 (1등급)</p>
              <p className="text-[26px] font-black text-[#ba1a1a] font-mono">
                {criticalCountVal}{' '}
                <span className="text-xs font-bold text-[#ba1a1a] font-sans">이번 달 +3건</span>
              </p>
            </div>
            <div className="p-3 bg-red-50 text-[#ba1a1a] rounded-lg group-hover:scale-110 transition-transform">
              <AlertTriangle className="w-6 h-6" />
            </div>
            <div className="absolute bottom-0 left-0 right-0 h-1 bg-[#ba1a1a]" />
          </div>
        </div>

        {/* ALWAYS show the inspection list content area with dynamic client-side pagination */}
        <div className="flex-1 flex flex-col justify-between">

            {/* INSPECTIONS CARD LIST */}
            <div className="space-y-5">
              {selectedReportId === '' ? (
                <div className="py-16 text-center bg-white rounded-xl border border-[#c3c6d3] shadow-xs">
                  <p className="text-sm font-bold text-[#ba1a1a]">보고서를 선택해 주세요.</p>
                  <p className="text-xs text-[#737782] mt-1">좌측 필터에서 보고서를 선택하시면 하수관로 결함 내역이 이곳에 표시됩니다.</p>
                </div>
              ) : currentItems.length === 0 ? (
                <div className="py-16 text-center bg-white rounded-xl border border-[#c3c6d3] shadow-xs">
                  <p className="text-sm font-semibold text-[#737782]">해당 조건의 하수관로 점검 내역이 없습니다.</p>
                  <p className="text-xs text-[#737782] mt-1">왼쪽의 필터 또는 날짜를 변경하시거나 '새로운 점검'을 해보세요.</p>
                </div>
              ) : (
                currentItems.map((item) => {
                  const isCritical = item.severity === 'critical';
                  return (
                    <div
                      key={item.id}
                      className="bg-white rounded-xl border border-[#c3c6d3] overflow-hidden flex flex-col lg:flex-row hover:shadow-md transition-shadow"
                    >
                      {/* Photo area (Left inside card) */}
                      <div className="w-full lg:w-[260px] bg-slate-900 relative shrink-0 aspect-video lg:aspect-auto flex items-center justify-center">
                        <img
                          src={item.imageUrl}
                          alt={item.defectType}
                          referrerPolicy="no-referrer"
                          onError={(event) => {
                            event.currentTarget.onerror = null;
                            event.currentTarget.src = PLACEHOLDER_IMAGE;
                          }}
                          className="w-full h-full object-cover"
                        />
                        {/* Severity Badge overlay */}
                        <div className="absolute top-3 left-3">
                          <span className={`text-[10px] font-black px-2 py-0.5 rounded-sm uppercase tracking-wider text-white shadow-md ${
                            isCritical ? 'bg-red-600' : 'bg-blue-600'
                          }`}>
                            {isCritical ? '심각' : '주의'}
                          </span>
                        </div>
                      </div>

                      {/* Text details (Middle inside card) */}
                      <div className="p-5 flex-1 flex flex-col justify-between border-b lg:border-b-0 lg:border-r border-[#ededf4] space-y-4">
                        <div>
                          <div className="flex items-center space-x-2">
                            <h4 className="text-lg font-black text-[#1a1b20]">{item.defectType}</h4>
                            <span className={`text-xs font-bold font-mono px-2 py-0.5 rounded-sm ${
                              isCritical ? 'text-[#ba1a1a] bg-red-50 border border-red-100' : 'text-[#0f458f] bg-blue-50 border border-blue-100'
                            }`}>
                              {isCritical ? '1등급 (심각)' : '2등급 (주의)'}
                            </span>
                            <span className="text-xs font-bold px-2 py-0.5 rounded-sm text-[#434751] bg-[#f3f3fa] border border-[#c3c6d3]">
                              {getRepairStatus(item.status)}
                            </span>
                          </div>

                          {/* Defect and repair information */}
                          <div className="grid grid-cols-2 gap-x-4 gap-y-2 mt-4 text-xs">
                            <div className="bg-[#f3f3fa] p-2 rounded-lg">
                              <p className="text-[#737782] font-semibold text-[10px] leading-none mb-1">날짜</p>
                              <p className="text-[#1a1b20] font-mono font-bold">{item.date}</p>
                            </div>
                            <div className="bg-[#f3f3fa] p-2 rounded-lg">
                              <p className="text-[#737782] font-semibold text-[10px] leading-none mb-1">시간</p>
                              <p className="text-[#1a1b20] font-mono font-bold">{item.time}</p>
                            </div>
                            <div className="col-span-2 bg-[#f3f3fa] p-2 rounded-lg">
                              <p className="text-[#737782] font-semibold text-[10px] leading-none mb-1">위치 구간</p>
                              <p className="text-[#1a1b20] font-mono font-bold">{item.location}</p>
                            </div>
                            <div className="col-span-2 bg-[#f3f3fa] p-2 rounded-lg">
                              <p className="text-[#737782] font-semibold text-[10px] leading-none mb-1">결함 내역</p>
                              <p className="text-[#1a1b20] font-bold">{item.defectType}</p>
                            </div>
                          </div>
                        </div>

                        <div className="flex items-center space-x-2 pt-2">
                          <button
                            onClick={() => onUpdateInspection(item.id, {
                              status: getNextRepairStatus(item.status),
                            })}
                            className="flex-1 py-2 text-xs font-bold text-[#434751] hover:text-[#1a1b20] bg-[#f3f3fa] hover:bg-[#e2e2e9] border border-[#c3c6d3] rounded-lg transition-colors cursor-pointer"
                          >
                            {getRepairButtonLabel(item.status)}
                          </button>
                          <button
                            onClick={() => openThreeDAnalysis(item)}
                            disabled={!threeDAnalysisUrl}
                            title={threeDAnalysisUrl ? '3D 분석 서버로 이동' : '3D 분석 서버 준비 중'}
                            className="flex-1 py-2 text-xs font-bold text-white bg-[#0f458f] hover:bg-[#0a336c] rounded-lg transition-all shadow-sm cursor-pointer hover:shadow-md disabled:bg-slate-400 disabled:hover:bg-slate-400 disabled:cursor-not-allowed disabled:shadow-none"
                          >
                            3D 분석
                          </button>
                        </div>
                      </div>

                      {/* Timeline section (Right inside card) */}
                      <div className="p-5 lg:w-[320px] bg-[#f9f9ff] shrink-0 flex flex-col justify-between">
                        <div>
                          <div className="flex justify-between items-center pb-2 border-b border-[#e2e2e9] mb-3">
                            <span className="text-[10px] font-bold text-[#737782] uppercase tracking-wider">점검 로그 타임라인</span>
                            <span className="text-[10px] font-bold text-[#0f458f]">이상 징후 탐지 이력</span>
                          </div>

                          {/* Custom Timeline stack */}
                          <div className="relative border-l border-slate-300 ml-2.5 pl-4 space-y-4">
                            {item.timeline.map((event) => {
                              const isCapture = event.iconType === 'capture';
                              return (
                                <div key={event.id} className="relative">
                                  {/* Dot Icon marker */}
                                  <span className={`absolute -left-[22.5px] top-0 flex items-center justify-center w-4.5 h-4.5 rounded-full border border-white text-[8px] font-black text-white ${
                                    event.iconType === 'start'
                                      ? 'bg-blue-600'
                                      : event.iconType === 'hazard'
                                      ? 'bg-amber-500'
                                      : event.iconType === 'capture'
                                      ? 'bg-red-600 animate-pulse'
                                      : 'bg-slate-400'
                                  }`}>
                                    {event.iconType === 'start' ? '▶' : event.iconType === 'hazard' ? '⚠' : event.iconType === 'capture' ? '📷' : '●'}
                                  </span>

                                  {/* Timeline Content Text */}
                                  <div className="flex flex-col">
                                    <div className="flex items-center justify-between">
                                      <h5 className={`text-[11px] font-bold ${isCapture ? 'text-red-600' : 'text-[#1a1b20]'}`}>
                                        {event.title}
                                      </h5>
                                      <span className="text-[9px] font-mono text-[#737782]">{event.time}</span>
                                    </div>
                                    <p className="text-[10px] text-[#434751] mt-0.5 leading-relaxed">
                                      {event.desc}
                                    </p>
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        </div>

                        <div className="text-[10px] text-[#737782] italic text-right mt-3 font-medium">
                          구간 기준 마일스톤 완료
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </div>

            {/* PAGINATION (Footer block) */}
            {selectedReportId !== '' && (
              <div className="flex flex-col sm:flex-row justify-between items-center pt-4 border-t border-[#e2e2e9] text-xs text-[#737782] gap-4">
                <span>전체 {totalItems}건 중 {startIndex}-{endIndex}건 표시 중</span>

                <div className="flex items-center space-x-1 font-semibold">
                  <button
                    onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                    disabled={currentPage === 1}
                    className="w-8 h-8 flex items-center justify-center border border-[#c3c6d3] rounded-lg bg-white hover:bg-[#f3f3fa] disabled:opacity-40 cursor-pointer text-[#434751]"
                    title="이전 페이지"
                  >
                    &lt;
                  </button>
                  {Array.from({ length: totalPages }).map((_, i) => {
                    const p = i + 1;
                    return (
                      <button
                        key={p}
                        onClick={() => setCurrentPage(p)}
                        className={`w-8 h-8 flex items-center justify-center rounded-lg font-bold transition-all cursor-pointer ${
                          currentPage === p
                            ? 'bg-[#0f458f] text-white'
                            : 'border border-[#c3c6d3] bg-white hover:bg-[#f3f3fa] text-[#434751]'
                        }`}
                      >
                        {p}
                      </button>
                    );
                  })}
                  <button
                    onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                    disabled={currentPage === totalPages}
                    className="w-8 h-8 flex items-center justify-center border border-[#c3c6d3] rounded-lg bg-white hover:bg-[#f3f3fa] disabled:opacity-40 cursor-pointer text-[#434751]"
                    title="다음 페이지"
                  >
                    &gt;
                  </button>
                </div>
              </div>
            )}
          </div>

      </main>
    </div>
  );
}
