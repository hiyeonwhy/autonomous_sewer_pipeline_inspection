import { AppTab } from '../types';
import { Droplet } from 'lucide-react';

interface HeaderProps {
  activeTab: AppTab;
  setActiveTab: (tab: AppTab) => void;
  unreadNotificationsCount: number;
}

export default function Header({ activeTab, setActiveTab }: HeaderProps) {
  return (
    <header className="sticky top-0 z-50 flex items-center justify-between h-[72px] px-6 bg-white border-b border-[#e2e2e9] shadow-sm">
      {/* Brand Logo & Name */}
      <div className="flex items-center space-x-3">
        <div className="flex items-center justify-center w-10 h-10 bg-[#0f458f] rounded-lg shadow-md transition-transform hover:scale-105">
          <Droplet className="w-5 h-5 text-white stroke-[2.5]" />
        </div>
        <div>
          <h1 className="text-[17px] font-bold text-[#1a1b20] leading-tight tracking-tight">
            스마트 하수관로 관리 시스템
          </h1>
          <p className="text-[10px] font-medium tracking-widest text-[#737782] uppercase">
            Smart Sewer Inspection System
          </p>
        </div>
      </div>

      {/* Main Navigation */}
      <nav className="flex h-full space-x-8">
        <button
          onClick={() => setActiveTab('realtime')}
          className={`relative flex items-center h-full px-2 text-sm font-semibold transition-colors duration-200 cursor-pointer ${
            activeTab === 'realtime' ? 'text-[#0f458f]' : 'text-[#737782] hover:text-[#1a1b20]'
          }`}
        >
          <span>실시간 점검</span>
          {activeTab === 'realtime' && (
            <div className="absolute bottom-0 left-0 right-0 h-[3px] bg-[#0f458f] rounded-t-full" />
          )}
        </button>
        <button
          onClick={() => setActiveTab('report')}
          className={`relative flex items-center h-full px-2 text-sm font-semibold transition-colors duration-200 cursor-pointer ${
            activeTab === 'report' ? 'text-[#0f458f]' : 'text-[#737782] hover:text-[#1a1b20]'
          }`}
        >
          <span>점검 보고서</span>
          {activeTab === 'report' && (
            <div className="absolute bottom-0 left-0 right-0 h-[3px] bg-[#0f458f] rounded-t-full" />
          )}
        </button>
      </nav>

      {/* Right Controls */}
      <div className="flex items-center">
        {/* User Info */}
        <div className="flex items-center space-x-2.5">
          <div className="flex items-center justify-center w-8 h-8 rounded-full bg-[#cadaff] text-[#0f458f] text-xs font-bold shadow-inner">
            AD
          </div>
          <span className="text-[13px] font-semibold text-[#1a1b20]">
            관리자 <span className="text-[#737782] font-normal">님</span>
          </span>
        </div>
      </div>
    </header>
  );
}
