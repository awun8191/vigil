import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { RotateCw } from 'lucide-react';
import { MessageStream } from '@/components/chat/MessageStream';
import { InputPill } from '@/components/chat/InputPill';
import { ThinkingCapsule } from '@/components/chat/ThinkingCapsule';
import { SparkMark } from '@/components/layout/SparkMark';
import { useCardTitles } from '@/lib/suggestions';
import { useChatStream, useCreateSession, useMessages, useSessions } from '@/queries/chat';

interface ChatLocationState {
  seed?: string;
  cardId?: string;
}

export default function ChatRoute() {
  const { sessionId: routeSessionId } = useParams<{ sessionId: string }>();
  const location = useLocation();
  const navigate = useNavigate();

  const state = (location.state ?? {}) as ChatLocationState;
  const [seed, setSeed] = useState<string | undefined>(state.seed);
  const [createdSessionId, setCreatedSessionId] = useState<string | null>(null);

  const sessions = useSessions();
  const createSession = useCreateSession();
  const creatingRef = useRef(false);

  const suggestions = useCardTitles(3);

  const activeSessionId =
    routeSessionId ?? createdSessionId ?? sessions.data?.[0]?.session_id ?? null;

  const messagesQuery = useMessages(activeSessionId);
  const stream = useChatStream(activeSessionId);

  // A first-run user with no sessions gets one, silently.
  useEffect(() => {
    if (routeSessionId || createdSessionId) return;
    if (!sessions.data || sessions.data.length > 0) return;
    if (creatingRef.current) return;

    creatingRef.current = true;
    createSession.mutate(
      { title: undefined, scope: 'chat:create-session' },
      { onSuccess: (session) => setCreatedSessionId(session.session_id) },
    );
  }, [routeSessionId, createdSessionId, sessions.data, createSession]);

  const handleSeedConsumed = useCallback(() => {
    setSeed(undefined);
    if (state.cardId) navigate('/chat', { replace: true, state: null });
  }, [navigate, state.cardId]);

  const messages = messagesQuery.data ?? [];
  const showThinking = stream.streaming && stream.streamedText.length === 0;

  return (
    <div className="flex h-dvh flex-col">
      <MessageStream
        messages={messages}
        streamedText={stream.streamedText}
        streaming={stream.streaming}
        trailing={
          showThinking ? <ThinkingCapsule active /> : null
        }
        emptyHint={<EmptyChat suggestions={suggestions} onPick={stream.send} />}
      />

      {stream.connection === 'reconnecting' ? (
        <div className="mx-auto w-full max-w-2xl px-4">
          <div
            role="status"
            className="mb-2 flex items-center gap-2 rounded-[var(--r-control)] border border-hairline bg-card px-3 py-2 text-[12px] text-ink-3"
          >
            <RotateCw size={13} strokeWidth={1.5} className="motion-safe:animate-spin" aria-hidden="true" />
            Reconnecting to Vigil...
          </div>
        </div>
      ) : null}

      {messagesQuery.isError ? (
        <div className="mx-auto w-full max-w-2xl px-4">
          <p role="alert" className="mb-2 px-2 text-[12px] text-ink-3">
            This conversation could not be loaded.
          </p>
        </div>
      ) : null}

      <InputPill
        onSend={stream.send}
        onStop={stream.stop}
        streaming={stream.streaming}
        disabled={!activeSessionId}
        {...(seed ? { seed } : {})}
        onSeedConsumed={handleSeedConsumed}
      />
    </div>
  );
}

function EmptyChat({
  suggestions,
  onPick,
}: {
  suggestions: string[];
  onPick: (text: string) => void;
}) {
  return (
    <div className="flex flex-col items-center px-4 pt-24 text-center">
      <SparkMark size={20} className="text-accent" />
      <h1 className="mt-4 text-[22px] font-semibold tracking-[-0.015em]">What are we working on?</h1>
      <p className="mt-1.5 text-[14px] text-ink-3">
        Ask anything, or hand Vigil a task it can run end to end.
      </p>

      {suggestions.length > 0 ? (
        <ul className="mt-8 w-full max-w-md space-y-2">
          {suggestions.map((suggestion) => (
            <li key={suggestion}>
              <button
                type="button"
                onClick={() => onPick(suggestion)}
                className="w-full rounded-[var(--r-control)] border border-hairline bg-card px-4 py-3 text-left text-[14px] text-ink-2 transition-colors duration-[var(--dur-base)] hover:border-hairline-strong hover:text-ink"
              >
                {suggestion}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
