import { ArrowUp, Mic, Paperclip, Plus, Square, X } from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useRef, useState } from 'react';

const spring = { type: 'spring' as const, stiffness: 400, damping: 30 };

function Waveform() {
  return (
    <div className="flex h-5 items-center gap-[3px]" aria-label="Recording">
      {[0, 1, 2, 3, 4].map((i) => (
        <span
          key={i}
          className="h-full w-[3px] origin-center animate-wave rounded-full bg-[var(--accent)]"
          style={{ animationDelay: `${i * 0.12}s` }}
        />
      ))}
    </div>
  );
}

interface Props {
  onSend: (text: string) => void;
  onStop: () => void;
  streaming: boolean;
  disabled?: boolean;
  seed?: string;
  onSeedConsumed?: () => void;
}

export function InputPill({
  onSend,
  onStop,
  streaming,
  disabled = false,
  seed,
  onSeedConsumed,
}: Props) {
  const [draft, setDraft] = useState('');
  const [files, setFiles] = useState<string[]>([]);
  const [recording, setRecording] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const areaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!seed) return;
    setDraft(seed);
    onSeedConsumed?.();
    areaRef.current?.focus();
  }, [seed, onSeedConsumed]);

  // Auto-expand the textarea up to a comfortable ceiling.
  useEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    el.style.height = '0px';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [draft]);

  const canSend = (draft.trim().length > 0 || files.length > 0) && !streaming && !disabled;

  const submit = () => {
    if (!canSend) return;
    onSend(draft);
    setDraft('');
    setFiles([]);
    setNotice(null);
  };

  const toggleRecording = () => {
    if (recording) {
      setRecording(false);
      // Capture is real, transcription is not wired to the backend. Say so rather than inventing text.
      setNotice('Voice captured. Transcription is not connected yet.');
      areaRef.current?.focus();
      return;
    }
    setNotice(null);
    setRecording(true);
  };

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-24 z-30 flex justify-center px-4 md:bottom-8 md:pl-20">
      <div className="pointer-events-auto w-full max-w-2xl">
        <AnimatePresence>
          {files.length > 0 && (
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 8 }}
              transition={spring}
              className="mb-2 flex flex-wrap gap-2 px-2"
            >
              {files.map((f) => (
                <span
                  key={f}
                  className="glass inline-flex items-center gap-1.5 rounded-full py-1 pl-3 pr-1.5 text-xs text-[var(--text-muted)]"
                >
                  <Paperclip size={12} strokeWidth={1.5} />
                  {f}
                  <button
                    type="button"
                    aria-label={`Remove ${f}`}
                    onClick={() => setFiles((fs) => fs.filter((x) => x !== f))}
                    className="flex h-5 w-5 items-center justify-center rounded-full hover:bg-[color-mix(in_srgb,var(--text)_10%,transparent)]"
                  >
                    <X size={12} strokeWidth={1.5} />
                  </button>
                </span>
              ))}
            </motion.div>
          )}
        </AnimatePresence>

        {notice ? (
          <p role="status" className="mb-2 px-3 text-[12px] text-[var(--text-muted)]">
            {notice}
          </p>
        ) : null}

        <div className="glass flex items-end gap-2 rounded-[28px] p-2">
          <input
            ref={fileRef}
            type="file"
            multiple
            hidden
            onChange={(e) => {
              const names = Array.from(e.target.files ?? []).map((f) => f.name);
              setFiles((fs) => [...fs, ...names]);
              e.target.value = '';
            }}
          />
          <button
            type="button"
            aria-label="Attach a file"
            onClick={() => fileRef.current?.click()}
            disabled={disabled}
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-[color-mix(in_srgb,var(--text)_8%,transparent)] text-[var(--text-muted)] transition-transform duration-300 ease-spring hover:scale-105 hover:text-[var(--text)] active:scale-95 disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            <Plus size={18} strokeWidth={1.5} />
          </button>

          <div className="flex min-h-11 flex-1 items-center">
            {recording ? (
              <div className="flex items-center gap-3 px-2 text-sm text-[var(--text-muted)]">
                <Waveform />
                Listening...
              </div>
            ) : (
              <label htmlFor="vigil-composer" className="sr-only">
                Message Vigil
              </label>
            )}
            {recording ? null : (
              <textarea
                id="vigil-composer"
                ref={areaRef}
                rows={1}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    submit();
                  }
                }}
                placeholder="Ask Vigil anything or assign a task..."
                disabled={disabled}
                className="max-h-[160px] w-full resize-none bg-transparent px-2 py-[10px] text-[15px] leading-relaxed text-[var(--text)] outline-none placeholder:text-[var(--text-muted)] disabled:opacity-60"
              />
            )}
          </div>

          <button
            type="button"
            aria-label={recording ? 'Stop recording' : 'Record voice'}
            aria-pressed={recording}
            onClick={toggleRecording}
            disabled={disabled}
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-[var(--text-muted)] transition-transform duration-300 ease-spring hover:scale-105 hover:text-[var(--text)] active:scale-95 disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            style={
              recording
                ? {
                    color: 'var(--accent)',
                    background: 'color-mix(in srgb, var(--accent) 14%, transparent)',
                  }
                : undefined
            }
          >
            <Mic size={18} strokeWidth={1.5} />
          </button>

          <button
            type="button"
            aria-label={streaming ? 'Stop generating' : 'Send'}
            disabled={!streaming && !canSend}
            onClick={streaming ? onStop : submit}
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full transition-all duration-300 ease-spring active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            style={
              streaming || canSend
                ? {
                    background: 'var(--accent)',
                    color: 'var(--accent-ink)',
                    boxShadow: '0 0 24px color-mix(in srgb, var(--accent) 45%, transparent)',
                  }
                : {
                    background: 'color-mix(in srgb, var(--text) 8%, transparent)',
                    color: 'var(--text-muted)',
                  }
            }
          >
            {streaming ? <Square size={15} strokeWidth={2} /> : <ArrowUp size={18} strokeWidth={1.75} />}
          </button>
        </div>
      </div>
    </div>
  );
}

export default InputPill;
