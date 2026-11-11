import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { FolderPlus, MessageSquare } from 'lucide-react';

import { HarkTopChrome } from './HarkTopChrome';
import { HarkLeftRail } from './HarkLeftRail';
import { HarkSearchModal } from './HarkSearchModal';
import { HarkProfileSheet } from './HarkProfileSheet';
import { HarkProactivityPill } from './HarkProactivityPill';

import { MessageStream } from '@/components/chat/MessageStream';
import { InputPill } from '@/components/chat/InputPill';
import { ThinkingCapsule } from '@/components/chat/ThinkingCapsule';
import { useCards } from '@/queries/cards';
import { useAgentChat, useCreateSession, useMessages, useSessions } from '@/queries/chat';
import { compareCards, timeOfDayGreeting } from '@/lib/format';
import type { Card } from '@/types';

interface LocationState {
  seed?: string;
  cardId?: string;
}

const STICK_THRESHOLD = 120;

function EmptyBriefing({
  onCard,
  onPrompt,
}: {
  onCard: (card: Card) => void;
  onPrompt: (prompt: string) => void;
}) {
  const cards = useCards();
  const waiting = [...(cards.data?.items ?? [])]
    .filter((card) => !['done', 'dismissed', 'expired'].includes(card.status))
    .sort(compareCards)
    .slice(0, 3);

  const suggestions = [
    'What needs me today?',
    'Draft a reply to the last email',
    'Summarise the open work',
  ];

  return (
    <div className="pb-6 pt-6">
      <p className="text-[11.5px] font-medium uppercase tracking-[0.12em] text-[var(--text-muted)]">
        {new Date().toLocaleDateString(undefined, {
          weekday: 'long',
          month: 'long',
          day: 'numeric',
        })}
      </p>
      <h1 className="mt-3 text-[clamp(1.9rem,4vw,2.65rem)] font-semibold leading-[1.08] tracking-[-0.035em] text-[var(--text)]">
        {timeOfDayGreeting()}.
        <span className="block text-[var(--text-muted)]">What needs you?</span>
      </h1>

      {waiting.length > 0 ? (
        <div className="mt-7 space-y-2">
          {waiting.map((card) => {
            const action = card.actions[0];
            const verb =
              card.verb ||
              action?.verb ||
              (action?.action_type === 'reply'
                ? 'Draft'
                : action?.action_type === 'dismiss'
                  ? 'Clear'
                  : 'Review');
            return (
              <HarkProactivityPill
                key={card.id}
                verb={verb}
                summary={card.title}
                onVerbClick={() => onCard(card)}
                onPillClick={() => onCard(card)}
              />
            );
          })}
        </div>
      ) : (
        <div className="mt-7 flex flex-wrap gap-2">
          {suggestions.map((prompt) => (
            <button
              key={prompt}
              type="button"
              onClick={() => onPrompt(prompt)}
              className="rounded-full border border-[var(--border)] bg-[var(--card)] px-4 py-2 text-[13px] font-medium text-[var(--text-secondary)] transition-colors hover:border-[var(--border-strong)] hover:text-[var(--text)]"
            >
              {prompt}
            </button>
          ))}
        </div>
      )}

      <p className="mt-5 text-[12.5px] leading-relaxed text-[var(--text-muted)]">
        Ask below, or tap a verb to open a prepared conversation.
      </p>
    </div>
  );
}

export function HarkUnifiedApp() {
  const { sessionId: routeSessionId } = useParams<{ sessionId: string }>();
  const location = useLocation();
  const navigate = useNavigate();

  const [activeTab, setActiveTab] = useState<'chat' | 'projects'>('chat');
  const [leftRailMode, setLeftRailMode] = useState<'widgets' | 'clock' | 'collapsed'>(() => {
    if (typeof window === 'undefined') return 'widgets';
    return window.matchMedia('(min-width: 768px)').matches ? 'widgets' : 'collapsed';
  });
  const [searchOpen, setSearchOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);

  const state = (location.state ?? {}) as LocationState;
  const [seed, setSeed] = useState<string | undefined>(state.seed);
  const [createdSessionId, setCreatedSessionId] = useState<string | null>(null);

  const sessions = useSessions();
  const createSession = useCreateSession();
  const creatingRef = useRef(false);

  const activeSessionId =
    routeSessionId ?? createdSessionId ?? sessions.data?.[0]?.session_id ?? null;

  const messagesQuery = useMessages(activeSessionId);
  const chat = useAgentChat(activeSessionId);

  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const lastTopRef = useRef(0);

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
    if (state.cardId) {
      navigate(location.pathname.startsWith('/agent/chat') ? location.pathname : '/agent/chat', {
        replace: true,
        state: null,
      });
    }
  }, [navigate, state.cardId, location.pathname]);

  const handleScroll = () => {
    const node = scrollRef.current;
    if (!node) return;

    const top = node.scrollTop;
    const previous = lastTopRef.current;
    lastTopRef.current = top;
    const gap = node.scrollHeight - top - node.clientHeight;

    // A delayed scroll event from our own follow can arrive after the transcript has
    // grown, making a pinned view look hundreds of pixels from the bottom. Only an
    // actual upward move by the reader is allowed to unpin the transcript.
    if (top < previous - 1) {
      stickRef.current = gap < STICK_THRESHOLD;
    } else if (gap < STICK_THRESHOLD) {
      stickRef.current = true;
    }
  };

  useEffect(() => {
    if (!stickRef.current) return;
    const node = scrollRef.current;
    if (!node) return;
    node.scrollTo({ top: node.scrollHeight, behavior: 'auto' });
    lastTopRef.current = node.scrollTop;
  }, [messagesQuery.data?.length, chat.streamedText, chat.streaming]);

  // Artifacts, KaTeX and images settle after the first paint and grow the transcript.
  // Follow that growth only while the reader is already parked at the bottom.
  useEffect(() => {
    const node = scrollRef.current;
    const content = node?.firstElementChild;
    if (!node || !content) return;

    const observer = new ResizeObserver(() => {
      if (!stickRef.current) return;
      node.scrollTop = node.scrollHeight;
      lastTopRef.current = node.scrollTop;
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, []);

  const cycleRail = () => {
    const isDesktop = window.matchMedia('(min-width: 768px)').matches;
    setLeftRailMode((previous) => {
      if (!isDesktop) return previous === 'collapsed' ? 'widgets' : 'collapsed';
      if (previous === 'widgets') return 'clock';
      if (previous === 'clock') return 'collapsed';
      return 'widgets';
    });
  };

  const handleCreateNewProject = () => {
    createSession.mutate(
      { title: 'New Project', scope: 'chat:create-session' },
      {
        onSuccess: (session) => {
          navigate(`/agent/chat/${session.session_id}`);
          setActiveTab('chat');
        },
      },
    );
  };

  const handleSend = async (text: string) => {
    let sid = activeSessionId;
    if (!sid) {
      try {
        const newSession = await createSession.mutateAsync({
          title: text.slice(0, 35),
          scope: `chat:create-session:${Date.now()}`,
        });
        sid = newSession.session_id;
        setCreatedSessionId(sid);
      } catch (error) {
        console.error('Failed to create session', error);
        return;
      }
    }
    stickRef.current = true;
    void chat.send(text, sid ?? undefined);
  };

  const openCard = (card: Card) => {
    setActiveTab('chat');
    const prompt = `Help me with this: ${card.title}. ${card.summary ?? ''}`.trim();
    setSeed(prompt);
    if (!location.pathname.startsWith('/agent/chat')) navigate('/agent/chat');
  };

  const messages = messagesQuery.data ?? [];
  const showThinking = chat.streaming;
  const railOpen = leftRailMode !== 'collapsed';

  return (
    <div className="relative h-dvh w-full overflow-hidden">
      <HarkTopChrome
        activeTab={activeTab}
        onTabChange={setActiveTab}
        onHomeClick={cycleRail}
        onSearchClick={() => setSearchOpen(true)}
        onProfileClick={() => setProfileOpen(true)}
        leftRailMode={leftRailMode}
      />

      {railOpen ? (
        <div
          aria-hidden="true"
          onClick={() => setLeftRailMode('collapsed')}
          className="fixed inset-0 z-20 bg-black/45 backdrop-blur-[2px] md:hidden"
        />
      ) : null}

      <HarkLeftRail mode={leftRailMode} />

      <div
        aria-hidden="true"
        className="top-fade pointer-events-none fixed inset-x-0 top-0 z-20 h-32"
      />

      <main
        id="main"
        ref={scrollRef}
        onScroll={handleScroll}
        className="scroll-soft h-dvh overflow-y-auto overscroll-contain"
      >
        <div className={railOpen ? 'md:pl-[324px] md:pr-6' : ''}>
          <div className="mx-auto w-full max-w-[780px] px-4 pb-44 pt-28 sm:px-6 sm:pt-32">
            {activeTab === 'chat' ? (
              <MessageStream
                messages={messages}
                streamedText={chat.streamedText}
                streaming={chat.streaming}
                trailing={showThinking ? <ThinkingCapsule active steps={chat.steps} /> : null}
                emptyHint={
                  <EmptyBriefing
                    onCard={openCard}
                    onPrompt={(prompt) => {
                      setSeed(prompt);
                      if (!location.pathname.startsWith('/agent/chat')) navigate('/agent/chat');
                    }}
                  />
                }
              />
            ) : (
              <div className="w-full space-y-5 py-4">
                <div className="flex items-end justify-between gap-3 pb-1">
                  <div>
                    <h1 className="text-[26px] font-semibold tracking-[-0.03em] text-[var(--text)]">
                      Projects
                    </h1>
                    <p className="mt-1 text-[13px] text-[var(--text-muted)]">
                      Every conversation Vigil has kept for you.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={handleCreateNewProject}
                    className="inline-flex h-9 items-center gap-1.5 rounded-full bg-[var(--btn-primary-bg)] px-3.5 text-[12.5px] font-semibold text-[var(--btn-primary-ink)] transition-transform hover:scale-105 active:scale-95"
                  >
                    <FolderPlus size={14} strokeWidth={1.75} />
                    New project
                  </button>
                </div>

                {sessions.isLoading ? (
                  <div className="space-y-2">
                    {[0, 1, 2].map((index) => (
                      <div
                        key={index}
                        className="h-[70px] animate-pulse rounded-[18px] border border-[var(--border)] bg-[var(--card)] motion-reduce:animate-none"
                      />
                    ))}
                  </div>
                ) : (sessions.data ?? []).length === 0 ? (
                  <div className="rail-card flex flex-col items-center px-6 py-14 text-center">
                    <MessageSquare size={20} strokeWidth={1.5} className="text-[var(--text-muted)]" />
                    <p className="mt-3 text-[14px] font-medium text-[var(--text)]">
                      No projects yet.
                    </p>
                    <p className="mt-1 max-w-xs text-[12.5px] leading-relaxed text-[var(--text-muted)]">
                      Start a conversation and it will appear here, with everything the run touched.
                    </p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {(sessions.data ?? []).map((session) => {
                      const active = session.session_id === activeSessionId;
                      return (
                        <button
                          key={session.session_id}
                          type="button"
                          onClick={() => {
                            navigate(`/agent/chat/${session.session_id}`);
                            setActiveTab('chat');
                          }}
                          className={`flex w-full items-center justify-between gap-3 rounded-[18px] border p-3.5 text-left transition-all hover:border-[var(--border-strong)] ${
                            active
                              ? 'border-[var(--text)]/30 bg-[var(--card-strong)]'
                              : 'border-[var(--border)] bg-[var(--card)]'
                          }`}
                        >
                          <div className="flex min-w-0 items-center gap-3">
                            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[var(--panel-inset)] text-[var(--text)]">
                              <MessageSquare size={16} strokeWidth={1.6} />
                            </div>
                            <div className="min-w-0">
                              <div className="truncate text-[13.5px] font-semibold text-[var(--text)]">
                                {session.title || 'Untitled session'}
                              </div>
                              <div className="mt-0.5 flex items-center gap-2 text-[11px] text-[var(--text-muted)]">
                                <span>
                                  {new Date(session.updated_at).toLocaleDateString(undefined, {
                                    month: 'short',
                                    day: 'numeric',
                                  })}
                                </span>
                                <span aria-hidden="true">·</span>
                                <span>
                                  {session.message_count} message
                                  {session.message_count === 1 ? '' : 's'}
                                </span>
                              </div>
                            </div>
                          </div>
                          <span
                            aria-hidden="true"
                            className="shrink-0 font-mono text-[14px] font-light text-[var(--text-muted)]"
                          >
                            ⌝
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </main>

      <div
        className={`pointer-events-none fixed bottom-4 z-40 flex flex-col items-center px-4 sm:bottom-6 ${
          railOpen ? 'left-0 right-0 md:left-[324px] md:right-6' : 'inset-x-0'
        }`}
      >
        {chat.error ? (
          <div className="pointer-events-auto mb-2 flex w-full max-w-[780px] items-center justify-between rounded-full border border-rose-500/20 bg-rose-500/10 px-4 py-2 text-[12px] text-rose-500 backdrop-blur-md">
            <span>{chat.error}</span>
            <button
              type="button"
              onClick={() => chat.retry()}
              className="ml-2 font-medium underline underline-offset-2 hover:text-rose-600"
            >
              Retry
            </button>
          </div>
        ) : null}
        <div className="pointer-events-auto w-full max-w-[780px]">
          <InputPill
            onSend={handleSend}
            onStop={() => chat.stop()}
            streaming={chat.streaming}
            seed={seed}
            onSeedConsumed={handleSeedConsumed}
          />
        </div>
      </div>

      <HarkSearchModal
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
        onSearch={(query) => {
          const trimmed = query.trim();
          setSearchOpen(false);
          if (trimmed) void handleSend(trimmed);
        }}
      />

      <HarkProfileSheet
        open={profileOpen}
        onClose={() => setProfileOpen(false)}
        onNavigate={(section) => {
          setProfileOpen(false);
          navigate(`/agent/settings/${section}`);
        }}
        onSignOut={() => navigate('/pair')}
      />
    </div>
  );
}

export default HarkUnifiedApp;
