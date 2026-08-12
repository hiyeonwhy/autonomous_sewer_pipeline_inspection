import { useState } from 'react';
import { ACTION_TYPES, InspectionItem, STATUS_OPTIONS } from '../types';
import { X, CheckCircle, AlertTriangle, ShieldCheck, Printer, FileText, Info, Compass, MapPin } from 'lucide-react';
import { motion } from 'motion/react';
import { PLACEHOLDER_IMAGE } from '../api/media';

interface DetailModalProps {
  key?: string | number;
  item: InspectionItem | null;
  onClose: () => void;
  onActionSubmit?: (id: string, actionType: string, status: string) => void;
}

export default function DetailModal({ item, onClose, onActionSubmit }: DetailModalProps) {
  if (!item) return null;

  // Selected treatment option state (조치 유형 선택)
  const [selectedActionType, setSelectedActionType] = useState<string>(
    ACTION_TYPES.includes(item.actionType as typeof ACTION_TYPES[number]) ? item.actionType! : ACTION_TYPES[0]
  );
  // Selected progress status state (조치 상태 변경)
  const [selectedStatus, setSelectedStatus] = useState<string>(
    STATUS_OPTIONS.includes(item.status as typeof STATUS_OPTIONS[number]) ? item.status! : STATUS_OPTIONS[0]
  );

  // Handle immediate change for action type
  const handleActionTypeChange = (type: string) => {
    setSelectedActionType(type);
    if (onActionSubmit) {
      onActionSubmit(item.id, type, selectedStatus);
    }
  };

  // Handle immediate change for status
  const handleStatusChange = (status: string) => {
    setSelectedStatus(status);
    if (onActionSubmit) {
      onActionSubmit(item.id, selectedActionType, status);
    }
  };

  // Handle submit action
  const handleSubmit = () => {
    if (onActionSubmit) {
      onActionSubmit(item.id, selectedActionType, selectedStatus);
    }
    onClose();
  };

  const isJoint = item.defectType === '이음부 결합';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/55 backdrop-blur-xs">
      <motion.div
        initial={{ opacity: 0, y: 15, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 15, scale: 0.98 }}
        transition={{ duration: 0.25, ease: 'easeOut' }}
        className="relative w-full max-w-4xl bg-white rounded-2xl shadow-2xl border border-[#c3c6d3] overflow-hidden flex flex-col"
      >
        {/* Modal Header */}
        <div className="flex items-center justify-between p-4.5 border-b border-[#e2e2e9] bg-[#f9f9ff]">
          <div className="flex items-center space-x-2.5">
            <div className="p-2 bg-[#e0ecff] text-[#0f458f] rounded-lg">
              <FileText className="w-5 h-5 stroke-[2.2]" />
            </div>
            <div>
              <h2 className="text-base font-black text-[#1a1b20] tracking-tight">
                AI 하수관 결함 진단 상세분석
              </h2>
              <p className="text-[11px] text-[#737782] font-mono mt-0.5">
                MODEL: {item.aiModel} • SCAN_ID: {item.id}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-[#737782] hover:text-red-600 hover:bg-red-50 rounded-full transition-all cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Modal Content - Two Column Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 p-6 overflow-y-auto max-h-[75vh]">
          
          {/* LEFT COLUMN: Large Sewer Image with OSD camera details & target circle marker */}
          <div className="space-y-4">
            <div className="relative aspect-video w-full bg-[#0a0a0d] rounded-xl overflow-hidden border border-[#c3c6d3] shadow-md group">
              <img
                src={item.imageUrl}
                alt={item.defectType}
                referrerPolicy="no-referrer"
                onError={(event) => {
                  event.currentTarget.onerror = null;
                  event.currentTarget.src = PLACEHOLDER_IMAGE;
                }}
                className="w-full h-full object-cover select-none"
              />

              {/* Camera OSD Overlays to look ultra-professional */}
              <div className="absolute top-3 left-3 bg-black/55 text-white text-[9px] font-mono p-1.5 rounded space-y-0.5 leading-tight pointer-events-none">
                <p className="text-[#a5b4fc] font-bold">CAM: {item.camId || 'SW-D04-012'}</p>
                <p>UTC: {item.date} {item.time}</p>
              </div>
              
              <div className="absolute bottom-3 right-3 bg-black/65 text-white text-[9px] font-mono p-1.5 rounded pointer-events-none text-right">
                <p className="text-[#00ffcc] font-semibold">SPEED: 0.15 m/s</p>
                <p className="text-white/50 text-[8px]">{item.originalImageId || '#IMG_24905'}</p>
              </div>
            </div>

            {/* Parameter Cards Grid */}
            <div className="grid grid-cols-1 gap-3">
              <div className="bg-[#f3f3fa] p-3 rounded-lg border border-[#c3c6d3]/40">
                <span className="text-[10px] font-semibold text-[#737782] block">점검 시각</span>
                <span className="text-[12px] font-bold text-[#1a1b20] font-mono mt-1 block leading-none">{item.time}</span>
              </div>

            </div>


          </div>

          {/* RIGHT COLUMN: Diagnostic details & state modifiers exactly as requested */}
          <div className="space-y-5 flex flex-col justify-between">
            
            {/* Title Block & Badges */}
            <div>
              <div className="flex flex-wrap items-center gap-2 mb-1.5">
                <span className="px-2.5 py-0.5 bg-[#ffdad6] text-[#ba1a1a] text-[11px] font-black rounded-sm border border-[#ffb3b1]">
                  1등급 (심각)
                </span>
              </div>
              
              <h3 className="text-xl font-black text-[#1a1b20] tracking-tight">
                {item.defectType} 상세진단
              </h3>
            </div>

            {/* Quick Metadata Table */}
            <div className="bg-[#f9f9ff] border border-[#c3c6d3]/60 rounded-xl overflow-hidden shadow-inner">
              <div className="grid grid-cols-2 border-b border-[#c3c6d3]/45">
                <div className="p-3.5 border-r border-[#c3c6d3]/45 flex flex-col">
                  <span className="text-[10px] font-bold text-[#737782]">관경 / 제원</span>
                  <span className="text-xs font-extrabold text-[#1a1b20] mt-0.5">PVC (400 mm)</span>
                </div>
                <div className="p-3.5 flex flex-col">
                  <span className="text-[10px] font-bold text-[#737782]">결함 종류</span>
                  <span className="text-xs font-extrabold text-[#1a1b20] mt-0.5">{item.defectType}</span>
                </div>
              </div>
              
              <div>
                <div className="p-3.5 flex flex-col">
                  <span className="text-[10px] font-bold text-[#737782]">검출 엔진</span>
                  <span className="text-xs font-mono font-semibold text-[#0f458f] mt-0.5">{item.aiModel}</span>
                </div>
              </div>
            </div>

            {/* Option Selection Block 1: 조치 유형 선택 */}
            <div className="space-y-2">
              <label className="text-xs font-bold text-[#434751] block">🔧 조치 유형 선택</label>
              
              <div className="grid grid-cols-2 gap-2">
                {ACTION_TYPES.map((act) => {
                  const isActive = selectedActionType === act;
                  return (
                    <button
                      key={act}
                      onClick={() => handleActionTypeChange(act)}
                      className={`py-3 px-3 text-xs font-bold rounded-lg border transition-all text-center cursor-pointer ${
                        isActive
                          ? 'bg-[#0f458f] text-white border-[#0f458f] shadow-sm'
                          : 'bg-white hover:bg-slate-50 text-[#434751] border-[#c3c6d3]'
                      }`}
                    >
                      {act}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Option Selection Block 2: 조치 상태 변경 */}
            <div className="space-y-2">
              <label className="text-xs font-bold text-[#434751] block">📡 조치 상태 변경</label>
              
              <div className="grid grid-cols-3 gap-2">
                {STATUS_OPTIONS.map((st) => {
                  const isActive = selectedStatus === st;
                  return (
                    <button
                      key={st}
                      onClick={() => handleStatusChange(st)}
                      className={`py-2 px-3 text-xs font-bold rounded-lg border transition-all text-center cursor-pointer ${
                        isActive
                          ? 'bg-[#ba1a1a] text-white border-[#ba1a1a] shadow-xs'
                          : 'bg-[#f3f3fa] hover:bg-[#ededf4] text-[#434751] border-[#c3c6d3]/70'
                      }`}
                    >
                      {st}
                    </button>
                  );
                })}
              </div>
            </div>

          </div>
        </div>

        {/* Modal Footer with print/confirm actions */}
        <div className="flex items-center justify-between p-4.5 border-t border-[#e2e2e9] bg-[#f9f9ff]">
          <button
            onClick={() => window.print()}
            className="flex items-center space-x-1.5 px-4 py-2.5 text-xs font-bold text-[#434751] hover:text-[#1a1b20] hover:bg-slate-100 rounded-lg transition-colors border border-[#c3c6d3] cursor-pointer"
          >
            <Printer className="w-4.5 h-4.5" />
            <span>분석표 인쇄</span>
          </button>

          <div className="flex space-x-2">
            <button
              onClick={onClose}
              className="px-4 py-2.5 text-xs font-bold text-[#434751] hover:bg-slate-100 rounded-lg transition-colors border border-transparent cursor-pointer"
            >
              취소
            </button>
            <button
              onClick={handleSubmit}
              className="flex items-center space-x-1.5 px-5 py-2.5 text-xs font-black text-white bg-[#0f458f] hover:bg-[#0a336c] rounded-lg transition-all shadow-md cursor-pointer hover:shadow-lg"
            >
              <CheckCircle className="w-4 h-4" />
              <span>보고서 반영 및 조치 등록</span>
            </button>
          </div>
        </div>

      </motion.div>
    </div>
  );
}
