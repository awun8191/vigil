import { AlertCircle, Check, Maximize2 } from 'lucide-react';
import { useState } from 'react';

interface Props {
  url: string;
  screenshotUrl?: string;
  status?: 'active' | 'error' | 'success';
  statusText: string;
  assistantNote?: string;
  savedLabel?: string;
}

export function HarkLiveBrowserCard({
  url,
  screenshotUrl,
  status = 'active',
  statusText,
  assistantNote,
  savedLabel,
}: Props) {
  const [fullscreen, setFullscreen] = useState(false);

  return (
    <div className={`my-3 transition-all ${fullscreen ? 'max-w-4xl' : 'max-w-2xl'}`}>
      {/* Browser Frame */}
      <div className="relative overflow-hidden rounded-[22px] bg-white border border-[var(--border)] shadow-[var(--shadow)]">
        <div className="flex h-8 items-center gap-1.5 border-b border-gray-100 bg-gray-50/80 px-3 text-[11px] text-gray-400">
          <div className="flex gap-1">
            <span className="h-2 w-2 rounded-full bg-gray-300" />
            <span className="h-2 w-2 rounded-full bg-gray-300" />
            <span className="h-2 w-2 rounded-full bg-gray-300" />
          </div>
          <span className="ml-2 truncate text-gray-500">{url}</span>
        </div>

        {/* Browser viewport simulation */}
        <div className={`relative flex w-full items-center justify-center bg-gray-50 p-4 ${fullscreen ? 'h-[440px]' : 'h-[280px]'}`}>
          {screenshotUrl ? (
            <img src={screenshotUrl} alt="Live browser preview" className="h-full w-full object-contain" />
          ) : (
            <div className="flex flex-col items-center justify-center rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
              <div className="text-[14px] font-semibold text-gray-800">Live browser preview unavailable</div>
            </div>
          )}

          {/* Bottom Right Floating Badge & Expand Button */}
          <div className="absolute bottom-3 right-3 flex items-center gap-1.5">
            <div
              className={`flex h-6 items-center gap-1.5 rounded-full px-2.5 text-[11px] font-semibold text-white ${
                status === 'error' ? 'bg-[#DC2626]' : 'bg-[#10B981]'
              }`}
            >
              {status === 'error' ? <AlertCircle size={12} /> : null}
              <span>{statusText}</span>
            </div>

            <button
              type="button"
              onClick={() => setFullscreen((v) => !v)}
              className="flex h-6 w-6 items-center justify-center rounded-full bg-black/80 text-white hover:bg-black"
            >
              <Maximize2 size={11} />
            </button>
          </div>
        </div>
      </div>

      {/* Accompanying Assistant Telemetry */}
      {assistantNote ? (
        <div className="hark-card mt-2.5 rounded-xl px-4 py-3 text-[13.5px] leading-relaxed text-[var(--text)]">
          {assistantNote}
        </div>
      ) : null}

      {savedLabel ? (
        <div className="mt-2 flex items-center gap-1.5 text-[12px] font-medium text-[var(--text-secondary)]">
          <Check size={13} className="text-[var(--text)]" />
          <span>{savedLabel}</span>
        </div>
      ) : null}
    </div>
  );
}

export default HarkLiveBrowserCard;
