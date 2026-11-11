import { ArrowUp, Mic, Paperclip, Plus, Square, X } from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useRef, useState } from 'react';
import { HandoffBeam, type BeamPhase } from '@/components/ui/handoff-beam';
import { API_BASE, getEffectiveBearerToken } from '@/lib/api';

const spring = { type: 'spring' as const, stiffness: 400, damping: 30 };


/**
 * Centre, edge, thinking, listening edge, speaking edge. The beam renders in mono so the composer
 * stays black and white in either theme.
 */

interface Props {
  onSend: (text: string) => void;
  onStop?: () => void;
  streaming?: boolean;
  busy?: boolean;
  /** True once the assistant has emitted at least one token, which separates thinking from speaking. */
  hasReplyTokens?: boolean;
  disabled?: boolean;
  seed?: string;
  onSeedConsumed?: () => void;
  className?: string;
}

export function InputPill({
  onSend,
  onStop = () => {},
  streaming,
  busy,
  hasReplyTokens = false,
  disabled = false,
  seed,
  onSeedConsumed,
  className = '',
}: Props) {
  const isStreaming = streaming ?? busy ?? false;
  const [draft, setDraft] = useState('');
  const [files, setFiles] = useState<string[]>([]);
  const [recording, setRecording] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [analyser, setAnalyser] = useState<AnalyserNode | null>(null);

  const areaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

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
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        try {
          mediaRecorderRef.current.stop();
        } catch {
          // cleanup
        }
      }
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

  const transcribeAudio = async (audioBlob: Blob) => {
    try {
      setNotice('Transcribing audio...');
      const formData = new FormData();
      formData.append('file', audioBlob, 'recording.webm');

      const headers: Record<string, string> = {};
      const token = getEffectiveBearerToken();
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }

      const res = await fetch(`${API_BASE}/voice/transcribe`, {
        method: 'POST',
        headers,
        body: formData,
      });

      const data = (await res.json().catch(() => null)) as {
        text?: string;
        mock?: boolean;
        message?: string;
      } | null;

      if (!res.ok) {
        throw new Error(data?.message ?? `Transcription failed (${res.status})`);
      }

      const transcribedText = data?.text?.trim();
      if (transcribedText) {
        setDraft((prev) => (prev ? `${prev.trim()} ${transcribedText}` : transcribedText));
        setNotice(
          data?.mock
            ? 'Simulated transcript — add GROQ_API_KEY in Settings → Connectors for real speech.'
            : null,
        );
        areaRef.current?.focus();
      } else {
        setNotice('No speech recognized.');
      }

    } catch (error) {
      setNotice(
        error instanceof Error
          ? error.message
          : 'Voice transcription failed. You can type your message instead.',
      );
    }
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

      chunksRef.current = [];
      if (typeof MediaRecorder !== 'undefined') {
        const recorder = new MediaRecorder(stream);
        recorder.ondataavailable = (event) => {
          if (event.data && event.data.size > 0) {
            chunksRef.current.push(event.data);
          }
        };
        recorder.start(100);
        mediaRecorderRef.current = recorder;
      }

      setRecording(true);
      setNotice(null);
    } catch {
      setNotice('Microphone permission was declined, so nothing is being captured.');
    }
  };

  const stopCapture = () => {
    setRecording(false);
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state !== 'inactive') {
      recorder.onstop = () => {
        const chunks = chunksRef.current;
        if (chunks.length > 0) {
          const audioBlob = new Blob(chunks, { type: 'audio/webm' });
          void transcribeAudio(audioBlob);
        }
      };
      try {
        recorder.stop();
      } catch {
        // Fallback
      }
    }
    mediaRecorderRef.current = null;
    releaseCapture();
  };


  const toggleRecording = () => {
    if (recording) {
      stopCapture();
      return;
    }
    setNotice(null);
    void startCapture();
  };

  const canSend = (draft.trim().length > 0 || files.length > 0) && !isStreaming && !disabled;

  const submit = () => {
    if (!canSend) return;
    onSend(draft);
    setDraft('');
    setFiles([]);
    setNotice(null);
  };

  const phase: BeamPhase = recording
    ? 'listening'
    : isStreaming
      ? hasReplyTokens
        ? 'speaking'
        : 'thinking'
      : 'idle';

  return (
    <div className={`relative w-full max-w-2xl mx-auto z-40 ${className}`}>
      <div className="w-full">
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
                  className="glass inline-flex items-center gap-1.5 rounded-[var(--r-inner)] py-1 pl-3 pr-1.5 text-xs text-[var(--text-muted)]"
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
          palette="mono"
          radius={20}
          className="composer-black"
          phaseLabels={{
            idle: 'Ready',
            listening: 'Listening',
            thinking: 'Vigil is thinking',
            speaking: 'Vigil is replying',
          }}
        >
          <div className="flex items-end gap-1.5 p-2">
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
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-[var(--text-muted)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)] active:scale-95 disabled:opacity-40"
            >
              <Plus size={18} strokeWidth={1.5} />
            </button>

            <div className="flex min-h-11 flex-1 items-center">
              {recording ? (
                <div className="flex items-center gap-3 px-2 text-[13px] text-[var(--text-muted)]">
                  <span className="relative flex h-2 w-2">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--text)] opacity-60" />
                    <span className="relative inline-flex h-2 w-2 rounded-full bg-[var(--text)]" />
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
                  placeholder="Ask Vigil anything..."
                  disabled={disabled}
                  className="max-h-[160px] w-full resize-none bg-transparent px-2 py-2 text-[15px] leading-relaxed text-[var(--text)] outline-none placeholder:text-[color-mix(in_srgb,var(--text)_52%,transparent)] disabled:opacity-60"
                />
              )}
            </div>

            <button
              type="button"
              aria-label={recording ? 'Stop recording' : 'Record voice'}
              aria-pressed={recording}
              onClick={toggleRecording}
              disabled={disabled}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-[var(--text-muted)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)] active:scale-95 disabled:opacity-40"
              style={
                recording
                  ? {
                      color: 'var(--text)',
                      background: 'color-mix(in srgb, var(--text) 14%, transparent)',
                    }
                  : undefined
              }
            >
              <Mic size={18} strokeWidth={1.5} />
            </button>

            <button
              type="button"
              aria-label={isStreaming ? 'Stop generating' : 'Send'}
              disabled={!isStreaming && !canSend}
              onClick={isStreaming ? () => onStop?.() : submit}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full transition-transform duration-200 ease-spring active:scale-95 disabled:opacity-45"
              style={
                isStreaming
                  ? {
                      background: 'var(--hark-control-bg)',
                      color: 'var(--hark-control-fg)',
                    }
                  : canSend
                    ? {
                        background: 'var(--hark-control-bg)',
                        color: 'var(--hark-control-fg)',
                      }
                    : {
                        background: 'color-mix(in srgb, var(--text) 8%, transparent)',
                        color: 'var(--text-muted)',
                      }
              }
            >
              {isStreaming ? (
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
