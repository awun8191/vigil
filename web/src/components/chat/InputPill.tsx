import {
  ArrowUp,
  FileCode2,
  FileSpreadsheet,
  FileText,
  Image as ImageIcon,
  Mic,
  Paperclip,
  Plus,
  Square,
  X,
} from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useRef, useState } from 'react';
import type { ClipboardEvent, DragEvent } from 'react';
import { HandoffBeam, type BeamPhase } from '@/components/ui/handoff-beam';
import { API_BASE, getEffectiveBearerToken } from '@/lib/api';

const spring = { type: 'spring' as const, stiffness: 400, damping: 30 };

interface PendingFile {
  id: string;
  file: File;
  previewUrl: string | null;
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function FileChipIcon({ name, mimeType }: { name: string; mimeType: string }) {
  const ext = name.includes('.') ? (name.split('.').pop()?.toLowerCase() ?? '') : '';
  if (mimeType.startsWith('image/') || ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'].includes(ext)) {
    return <ImageIcon size={13} strokeWidth={1.65} className="shrink-0 text-amber-400" />;
  }
  if (['xlsx', 'xlsm', 'csv', 'tsv'].includes(ext)) {
    return <FileSpreadsheet size={13} strokeWidth={1.65} className="shrink-0 text-emerald-400" />;
  }
  if (['html', 'htm', 'json', 'yaml', 'yml', 'py', 'ts', 'tsx', 'js', 'sh', 'sql'].includes(ext)) {
    return <FileCode2 size={13} strokeWidth={1.65} className="shrink-0 text-[var(--text-muted)]" />;
  }
  if (['pdf', 'docx', 'pptx', 'md', 'txt'].includes(ext)) {
    return <FileText size={13} strokeWidth={1.65} className="shrink-0 text-rose-400" />;
  }
  return <Paperclip size={12} strokeWidth={1.5} className="shrink-0" />;
}

/**
 * Centre, edge, thinking, listening edge, speaking edge. The beam renders in mono so the composer
 * stays black and white in either theme.
 */

interface Props {
  onSend: (text: string, files?: File[]) => void;
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
  const [files, setFiles] = useState<PendingFile[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const [recording, setRecording] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [analyser, setAnalyser] = useState<AnalyserNode | null>(null);

  const areaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const filesRef = useRef<PendingFile[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  useEffect(() => {
    filesRef.current = files;
  }, [files]);

  useEffect(() => {
    return () => {
      for (const item of filesRef.current) {
        if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
      }
    };
  }, []);

  const addFiles = (incoming: File[]) => {
    if (incoming.length === 0) return;
    setFiles((prev) => {
      const next = [...prev];
      for (const file of incoming) {
        const isDuplicate = next.some(
          (item) =>
            item.file.name === file.name &&
            item.file.size === file.size &&
            item.file.lastModified === file.lastModified,
        );
        if (isDuplicate) continue;
        const isImage = file.type.startsWith('image/');
        const previewUrl =
          isImage && typeof URL.createObjectURL === 'function'
            ? URL.createObjectURL(file)
            : null;
        next.push({
          id: `${file.name}-${file.size}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          file,
          previewUrl,
        });
      }
      return next;
    });
  };

  const removeFile = (id: string) => {
    setFiles((prev) => {
      const target = prev.find((item) => item.id === id);
      if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl);
      return prev.filter((item) => item.id !== id);
    });
  };

  const clearAllFiles = () => {
    setFiles((prev) => {
      for (const item of prev) {
        if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
      }
      return [];
    });
  };

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

  const handlePaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const items = Array.from(e.clipboardData?.items ?? []);
    const pastedFiles: File[] = [];
    for (const item of items) {
      if (item.kind === 'file') {
        const f = item.getAsFile();
        if (f) pastedFiles.push(f);
      }
    }
    if (pastedFiles.length > 0) {
      addFiles(pastedFiles);
    }
  };

  const handleDragOver = (e: DragEvent<HTMLDivElement>) => {
    if (!e.dataTransfer?.types?.includes('Files')) return;
    e.preventDefault();
    setDragActive(true);
  };

  const handleDragLeave = (e: DragEvent<HTMLDivElement>) => {
    if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
    setDragActive(false);
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    if (!e.dataTransfer?.files?.length) return;
    e.preventDefault();
    setDragActive(false);
    addFiles(Array.from(e.dataTransfer.files));
  };

  const canSend = (draft.trim().length > 0 || files.length > 0) && !isStreaming && !disabled;

  const submit = () => {
    if (!canSend) return;
    const rawFiles = files.map((item) => item.file);
    onSend(draft, rawFiles.length > 0 ? rawFiles : undefined);
    setDraft('');
    clearAllFiles();
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
    <div
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={`relative w-full max-w-2xl mx-auto z-40 ${className}`}
    >
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
              {files.map((item) => (
                <span
                  key={item.id}
                  className="glass inline-flex items-center gap-2 rounded-[var(--r-inner)] py-1 pl-2.5 pr-1.5 text-xs text-[var(--text)]"
                >
                  {item.previewUrl ? (
                    <img
                      src={item.previewUrl}
                      alt={item.file.name}
                      className="h-6 w-6 rounded-[6px] object-cover border border-[var(--border)]"
                    />
                  ) : (
                    <FileChipIcon name={item.file.name} mimeType={item.file.type} />
                  )}
                  <span className="max-w-[180px] truncate font-medium">{item.file.name}</span>
                  <span className="font-mono text-[10.5px] text-[var(--text-muted)]">
                    {formatBytes(item.file.size)}
                  </span>
                  <button
                    type="button"
                    aria-label={`Remove ${item.file.name}`}
                    onClick={() => removeFile(item.id)}
                    className="flex h-5 w-5 items-center justify-center rounded-full text-[var(--text-muted)] hover:bg-[color-mix(in_srgb,var(--text)_10%,transparent)] hover:text-[var(--text)]"
                  >
                    <X size={12} strokeWidth={1.5} />
                  </button>
                </span>
              ))}
            </motion.div>
          )}
        </AnimatePresence>

        {dragActive ? (
          <p role="status" className="mb-2 px-3 text-[12px] font-medium text-[var(--text)]">
            Drop files here to attach them to your message
          </p>
        ) : notice ? (
          <p role="status" className="mb-2 px-3 text-[12px] text-[var(--text-muted)]">
            {notice}
          </p>
        ) : null}

        <HandoffBeam
          phase={phase}
          analyser={recording ? analyser : null}
          palette="mono"
          radius={20}
          className={`composer-black ${dragActive ? 'ring-2 ring-[var(--text)]/40' : ''}`}
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
                const selected = Array.from(e.target.files ?? []);
                addFiles(selected);
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
                  onPaste={handlePaste}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      submit();
                    }
                  }}
                  placeholder={
                    files.length > 0
                      ? 'Add a message about your attached file(s), or press Enter to send...'
                      : 'Ask Vigil anything, or drop/paste files...'
                  }
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
