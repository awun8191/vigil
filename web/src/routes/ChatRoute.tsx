import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { AlertCircle, RotateCw } from 'lucide-react';
import { MessageStream } from '@/components/chat/MessageStream';
import { InputPill } from '@/components/chat/InputPill';
import { ThinkingCapsule } from '@/components/chat/ThinkingCapsule';
import { useCardTitles } from '@/lib/suggestions';
import { stepLabel } from '@/queries/runs';
import { useAgentChat, useCreateSession, useMessages, useSessions } from '@/queries/chat';

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
  const chat = useAgentChat(activeSessionId);

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
    if (state.cardId) navigate('/agent/chat', { replace: true, state: null });
  }, [navigate, state.cardId]);

  const messages = messagesQuery.data ?? [];
  const showThinking = chat.streaming;
  // The empty hint only renders once the history query has settled: while loading, an empty
  // array means "not fetched yet", and painting EmptyChat then displaces real history in a
  // visible flash when the query resolves.
  const historySettled = messagesQuery.isSuccess || messagesQuery.isError;

  return (
    <div className="flex h-dvh flex-col">
      <div className="min-h-0 flex-1">
        <MessageStream
          messages={messages}
          streamedText={chat.streamedText}
          streaming={chat.streaming}
          trailing={
            showThinking && !chat.awaitingApproval ? (
              <ThinkingCapsule
                active
                {...(chat.steps.length > 0
                  ? {
                      steps: chat.steps.slice(-6).map((step) => ({ label: stepLabel(step) })),
                    }
                  : {})}
              />
            ) : null
          }
          emptyHint={
            historySettled ? <EmptyChat suggestions={suggestions} onPick={chat.send} /> : null
          }
        />
      </div>

      {chat.connection === 'reconnecting' ? (
        <div className="pointer-events-none fixed inset-x-0 bottom-[11.5rem] z-20 flex justify-center px-4 md:bottom-[7.5rem] md:pl-20">
          <p className="glass inline-flex items-center gap-2 rounded-[8px] px-3.5 py-2 text-[12px] text-[var(--text-muted)]">
            <RotateCw size={13} strokeWidth={1.5} className="animate-spin" />
            Reconnecting to Vigil...
          </p>
        </div>
      ) : null}

      {chat.awaitingApproval ? (
        <div className="pointer-events-auto fixed inset-x-0 bottom-[11.5rem] z-20 flex justify-center px-4 md:bottom-[7.5rem] md:pl-20">
          <button
            type="button"
            onClick={() => navigate('/agent/approvals')}
            className="glass cursor-pointer rounded-[8px] px-3.5 py-2 text-[12px] text-[var(--text-muted)] transition-colors hover:text-[var(--text)]"
          >
            The agent is waiting on your approval, open Approvals to decide
          </button>
        </div>
      ) : null}

      {chat.error ? (
        <div className="pointer-events-none fixed inset-x-0 bottom-[11.5rem] z-20 flex justify-center px-4 md:bottom-[10rem] md:pl-20">
          <p className="glass inline-flex max-w-md items-center gap-2 rounded-[8px] px-3.5 py-2 text-[12px] text-[var(--text)]">
            <AlertCircle size={13} strokeWidth={1.5} />
            {chat.error}
          </p>
        </div>
      ) : null}

      <InputPill
        onSend={chat.send}
        onStop={chat.stop}
        streaming={chat.streaming}
        hasReplyTokens={chat.steps.length > 0}
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
    <div className="animate-rise flex flex-col items-center px-4 pt-8 text-center">
      <h1 className="text-[26px] font-semibold tracking-[-0.03em]">
        What are we working on?
      </h1>
      <p className="mt-2 max-w-sm text-[15px] leading-relaxed text-[var(--text-muted)]">
        Ask anything, or hand Vigil a task it can run end to end.
      </p>

      {suggestions.length > 0 ? (
        <ul className="mt-8 w-full max-w-md space-y-2">
          {suggestions.map((suggestion) => (
            <li key={suggestion}>
              <button
                type="button"
                onClick={() => onPick(suggestion)}
                className="card w-full rounded-[10px] px-4 py-3 text-left text-[14px] text-[var(--text)] transition-colors"
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
