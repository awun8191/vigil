import { ArrowUp, Mic, Paperclip, Plus, Square, X } from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useRef, useState } from 'react';
import { HandoffBeam, type BeamPhase } from '@/components/ui/handoff-beam';
import { useBjorkTone } from '@/components/ui/handoff-beam-utils/tone';

const spring = { type: 'spring' as const, stiffness: 400, damping: 30 };

/**
 * Centre, edge, thinking, listening edge, speaking edge. Two sets because the beam multiplies
 * its gain on light surfaces, and a pale sky blue washes out on cream.
 */
const BEAM_PALETTE: Record<'dark' | 'light', string[]> = {
  dark: ['#38bdf8', '#818cf8', '#a5b4fc', '#22d3ee', '#7dd3fc'],
  light: ['#0284c7', '#6366f1', '#818cf8', '#0891b2', '#0ea5e9'],
};

interface Props {
  onSend: (text: string) => void;
  onStop: () => void;
  streaming: boolean;
  /** True once the assistant has emitted at least one token, which separates thinking from speaking. */
  hasReplyTokens?: boolean;
  disabled?: boolean;
  seed?: string;
  onSeedConsumed?: () => void;
}

export function InputPill({
  onSend,
  onStop,
  streaming,
  hasReplyTokens = false,
  disabled = false,
  seed,
  onSeedConsumed,
}: Props) {
  const [draft, setDraft] = useState('');
  const [files, setFiles] = useState<string[]>([]);
  const [recording, setRecording] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [analyser, setAnalyser] = useState<AnalyserNode | null>(null);

  const tone = useBjorkTone();

  const areaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);

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

  // Microphone hardware must never outlive the component.
  useEffect(() => {
    return () => {
      streamRef.current?.getTracks().forEach((track) => track.stop());
      void ctxRef.current?.close();
    };
  }, []);

  const releaseCapture = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    void ctxRef.current?.close();
    ctxRef.current = null;
    setAnalyser(null);
  };

  const startCapture = async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setNotice('This browser exposes no microphone to the page.');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const ctx = new AudioContext();
      ctxRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const node = ctx.createAnalyser();
      node.fftSize = 1024;
      node.smoothingTimeConstant = 0.65;
      source.connect(node);

      setAnalyser(node);
      setRecording(true);
      setNotice(null);
    } catch {
      setNotice('Microphone permission was declined, so nothing is being captured.');
    }
  };

  const stopCapture = () => {
    setRecording(false);
    releaseCapture();
    setNotice('Microphone stopped. Transcription is not connected yet.');
  };

  const toggleRecording = () => {
    if (recording) {
      stopCapture();
      return;
    }
    setNotice(null);
    void startCapture();
  };

  const canSend = (draft.trim().length > 0 || files.length > 0) && !streaming && !disabled;

  const submit = () => {
    if (!canSend) return;
    onSend(draft);
    setDraft('');
    setFiles([]);
    setNotice(null);
  };

  const phase: BeamPhase = recording
    ? 'listening'
    : streaming
      ? hasReplyTokens
        ? 'speaking'
        : 'thinking'
      : 'idle';

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

        <HandoffBeam
          phase={phase}
          analyser={recording ? analyser : null}
          palette={BEAM_PALETTE[tone]}
          radius={28}
          className="glass"
          phaseLabels={{
            idle: 'Ready',
            listening: 'Listening',
            thinking: 'Vigil is thinking',
            speaking: 'Vigil is replying',
          }}
        >
          <div className="flex items-end gap-2 p-2">
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
                  <span className="relative flex h-2 w-2">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--accent)] opacity-60" />
                    <span className="relative inline-flex h-2 w-2 rounded-full bg-[var(--accent)]" />
                  </span>
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
              {streaming ? (
                <Square size={15} strokeWidth={2} />
              ) : (
                <ArrowUp size={18} strokeWidth={1.75} />
              )}
            </button>
          </div>
        </HandoffBeam>
      </div>
    </div>
  );
}

export default InputPill;
