import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUp, Mic, Plus, Square, X } from 'lucide-react';
import { IconButton } from '@/components/ui/IconButton';

interface InputPillProps {
  onSend: (text: string) => void;
  onStop: () => void;
  streaming: boolean;
  disabled?: boolean;
  seed?: string;
  onSeedConsumed?: () => void;
}

const MAX_ROWS_PX = 160;

/**
 * The floating hero input.
 *
 * Mobile offsets come from the stack in the spec: the pill sits at
 * `1rem + safe-area-inset-bottom`, the dock sits above it at `5.5rem + safe-area-inset-bottom`.
 * The two never share an offset.
 */
export function InputPill({
  onSend,
  onStop,
  streaming,
  disabled = false,
  seed,
  onSeedConsumed,
}: InputPillProps) {
  const [value, setValue] = useState('');
  const [recording, setRecording] = useState(false);
  const [recordError, setRecordError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // A card's `Discuss in Chat` handoff arrives through router state.
  useEffect(() => {
    if (seed) {
      setValue(seed);
      onSeedConsumed?.();
      textareaRef.current?.focus();
    }
  }, [seed, onSeedConsumed]);

  const resize = useCallback(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.style.height = 'auto';
    node.style.height = `${Math.min(node.scrollHeight, MAX_ROWS_PX)}px`;
  }, []);

  useEffect(resize, [value, resize]);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || streaming || disabled) return;
    onSend(trimmed);
    setValue('');
  };

  const toggleRecording = () => {
    if (recording) {
      setRecording(false);
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setRecordError('This browser does not expose microphone access.');
      return;
    }
    // Transcription is not wired to the backend yet. Recording is real, the text is not.
    setRecordError('Voice notes record, but transcription is not connected yet.');
    setRecording(true);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
    if (event.key === 'Escape' && recording) {
      setRecording(false);
    }
  };

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-30">
      <div className="mx-auto w-full max-w-2xl px-4 pb-[calc(1rem+env(safe-area-inset-bottom))]">
        {recordError ? (
          <p role="alert" className="mb-2 px-2 text-[12px] text-ink-3">
            {recordError}
          </p>
        ) : null}

        <div
          className="pointer-events-auto flex items-end gap-2 rounded-[var(--r-pill)] border border-hairline bg-chrome p-2 backdrop-blur-2xl"
          style={{ boxShadow: 'var(--shadow-chrome)' }}
        >
          <IconButton
            label="Attach a file"
            variant="plain"
            size="md"
            disabled={disabled}
            className="mb-0.5"
          >
            <Plus size={18} strokeWidth={1.5} aria-hidden="true" />
          </IconButton>

          <label htmlFor="vigil-composer" className="sr-only">
            Message Vigil
          </label>
          <textarea
            id="vigil-composer"
            ref={textareaRef}
            rows={1}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask Vigil anything, or assign a task..."
            aria-describedby="vigil-composer-help"
            disabled={disabled}
            className="max-h-[160px] flex-1 resize-none bg-transparent py-2.5 text-[15px] leading-relaxed text-ink outline-none placeholder:text-ink-3 disabled:opacity-60"
          />
          <span id="vigil-composer-help" className="sr-only">
            Enter to send, Shift plus Enter for a new line.
          </span>

          {recording ? <RecordingWaveform /> : null}

          <IconButton
            label={recording ? 'Stop recording' : 'Record a voice note'}
            variant="plain"
            size="md"
            className="mb-0.5"
            disabled={disabled}
            onClick={toggleRecording}
          >
            {recording ? (
              <X size={18} strokeWidth={1.5} aria-hidden="true" />
            ) : (
              <Mic size={18} strokeWidth={1.5} aria-hidden="true" />
            )}
          </IconButton>

          <IconButton
            label={streaming ? 'Stop generating' : 'Send message'}
            variant={value.trim() || streaming ? 'accent' : 'plain'}
            size="md"
            className="mb-0.5"
            disabled={disabled || (!streaming && !value.trim())}
            onClick={streaming ? onStop : submit}
          >
            {streaming ? (
              <Square size={16} strokeWidth={1.75} aria-hidden="true" />
            ) : (
              <ArrowUp size={18} strokeWidth={1.75} aria-hidden="true" />
            )}
          </IconButton>
        </div>
      </div>
    </div>
  );
}

/** Five bars driven by real microphone level. Not a decorative loop. */
function RecordingWaveform() {
  const [levels, setLevels] = useState<number[]>([0.2, 0.2, 0.2, 0.2, 0.2]);

  useEffect(() => {
    let stream: MediaStream | null = null;
    let frame = 0;
    let context: AudioContext | null = null;
    let cancelled = false;

    const start = async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        context = new AudioContext();
        const source = context.createMediaStreamSource(stream);
        const analyser = context.createAnalyser();
        analyser.fftSize = 64;
        source.connect(analyser);

        const data = new Uint8Array(analyser.frequencyBinCount);
        const tick = () => {
          analyser.getByteFrequencyData(data);
          const bins = [2, 5, 9, 14, 20].map((index) => (data[index] ?? 0) / 255);
          setLevels(bins.map((value) => Math.max(0.15, Math.min(1, value))));
          frame = requestAnimationFrame(tick);
        };
        tick();
      } catch {
        setLevels([0.15, 0.15, 0.15, 0.15, 0.15]);
      }
    };

    void start();

    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      stream?.getTracks().forEach((track) => track.stop());
      void context?.close();
    };
  }, []);

  return (
    <div className="mb-2.5 flex items-center gap-[3px]" aria-hidden="true">
      {levels.map((level, index) => (
        <span
          key={index}
          className="w-[3px] rounded-full bg-accent transition-[height] duration-[var(--dur-fast)]"
          style={{ height: `${6 + level * 16}px` }}
        />
      ))}
    </div>
  );
}
