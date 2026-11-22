import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import {
  FileText,
  FolderKanban,
  FolderPlus,
  MessageSquare,
  Trash2,
  Upload,
  X,
} from 'lucide-react';

import { HarkTopChrome } from './HarkTopChrome';
import { HarkLeftRail } from './HarkLeftRail';
import { HarkSearchModal } from './HarkSearchModal';
import { HarkProfileSheet } from './HarkProfileSheet';
import { HarkProactivityPill } from './HarkProactivityPill';

import { MessageStream } from '@/components/chat/MessageStream';
import { InputPill } from '@/components/chat/InputPill';
import { ThinkingCapsule } from '@/components/chat/ThinkingCapsule';
import { LiveBrowserCard, buildBrowserPreviews } from '@/components/chat/LiveBrowserCard';
import { useCards } from '@/queries/cards';
import {
  PRIMARY_SESSION_KEY,
  useAgentChat,
  useLoadEarlierMessages,
  useMessages,
  usePrimarySession,
} from '@/queries/chat';
import {
  useCreateProject,
  useDeleteProject,
  useProjectDetail,
  useProjects,
  useUpdateProject,
  useUploadProjectFiles,
} from '@/queries/projects';
import { api } from '@/lib/api';
import { useQueryClient } from '@tanstack/react-query';
import { compareCards, timeOfDayGreeting } from '@/lib/format';
import type { Card } from '@/types';

interface LocationState {
  seed?: string;
  cardId?: string;
}

const STICK_THRESHOLD = 120;

function EmptyBriefing({
  onCard,
  onCardVerb,
  onPrompt,
}: {
  onCard: (card: Card) => void;
  onCardVerb: (card: Card) => void;
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
        <span className="block text-[var(--text-muted)]">How can I help?</span>
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
                onVerbClick={() => onCardVerb(card)}
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
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [creatingProject, setCreatingProject] = useState(false);
  const [newProjectName, setNewProjectName] = useState('');
  const [newProjectDescription, setNewProjectDescription] = useState('');
  const [newProjectInstructions, setNewProjectInstructions] = useState('');
  const [editInstructions, setEditInstructions] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const queryClient = useQueryClient();

  const primarySession = usePrimarySession();
  const projectsQuery = useProjects();
  const projectDetailQuery = useProjectDetail(selectedProjectId);
  const createProjectMutation = useCreateProject();
  const updateProjectMutation = useUpdateProject();
  const deleteProjectMutation = useDeleteProject();
  const uploadProjectFilesMutation = useUploadProjectFiles();
  const projectFileInputRef = useRef<HTMLInputElement>(null);

  const activeSessionId =
    routeSessionId ?? primarySession.data?.session_id ?? null;

  const activeProject = useMemo(
    () => (projectsQuery.data ?? []).find((p) => p.id === activeProjectId) ?? null,
    [projectsQuery.data, activeProjectId],
  );

  useEffect(() => {
    if (!projectDetailQuery.data) return;
    setEditDescription(projectDetailQuery.data.description ?? '');
    setEditInstructions(projectDetailQuery.data.instructions ?? '');
  }, [projectDetailQuery.data]);

  const messagesQuery = useMessages(activeSessionId);
  const { loadEarlier, loadingEarlier, hasMore } = useLoadEarlierMessages(activeSessionId);
  const chat = useAgentChat(activeSessionId, activeProjectId);

  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const lastTopRef = useRef(0);

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

  const handleSend = async (text: string, files?: File[]) => {
    let sid = activeSessionId;
    if (!sid) {
      try {
        const primary = await api.getPrimarySession();
        sid = primary.session_id;
        queryClient.setQueryData(PRIMARY_SESSION_KEY, primary);
      } catch (error) {
        console.error('Failed to resolve primary session', error);
        return;
      }
    }
    stickRef.current = true;
    void chat.send(text, sid ?? undefined, files);
  };

  const openCard = (card: Card) => {
    setActiveTab('chat');
    const replyAction = card.actions.find((a) => a.action_type === 'reply');
    const seedText =
      typeof replyAction?.payload?.seed === 'string' && replyAction.payload.seed
        ? replyAction.payload.seed
        : `Help me with this: ${card.title}. ${card.summary ?? ''}`.trim();
    setSeed(seedText);
    if (!location.pathname.startsWith('/agent/chat')) navigate('/agent/chat');
  };

  const executeCardVerb = (card: Card) => {
    setActiveTab('chat');
    const primary = card.actions[0];
    const initialPrompt =
      typeof primary?.payload?.initial_prompt === 'string' && primary.payload.initial_prompt
        ? primary.payload.initial_prompt
        : `Help me with this: ${card.title}. ${card.summary ?? ''}`.trim();
    if (!location.pathname.startsWith('/agent/chat')) navigate('/agent/chat');
    void handleSend(initialPrompt);
  };

  const messages = messagesQuery.data ?? [];
  const showThinking = chat.streaming;
  const railOpen = leftRailMode !== 'collapsed';

  const liveBrowserPreview = useMemo(() => {
    if (!chat.streaming) return null;
    const previews = buildBrowserPreviews(chat.steps);
    return previews.length > 0 ? previews[previews.length - 1] ?? null : null;
  }, [chat.streaming, chat.steps]);

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
              <div className="space-y-4">
                {messages.length >= 100 && hasMore ? (
                  <div className="flex justify-center">
                    <button
                      type="button"
                      onClick={() => void loadEarlier()}
                      disabled={loadingEarlier}
                      className="rounded-full border border-[var(--border)] bg-[var(--card)] px-3.5 py-1.5 text-[12px] font-medium text-[var(--text-muted)] transition-colors hover:border-[var(--border-strong)] hover:text-[var(--text)] disabled:opacity-50"
                    >
                      {loadingEarlier ? 'Loading earlier turns...' : 'Load earlier messages'}
                    </button>
                  </div>
                ) : null}
                <MessageStream
                  messages={messages}
                  streamedText={chat.streamedText}
                  streaming={chat.streaming}
                  trailing={
                    showThinking ? (
                      <div className="w-full min-w-0 space-y-2">
                        <ThinkingCapsule active steps={chat.steps} />
                        {liveBrowserPreview ? (
                          <LiveBrowserCard preview={liveBrowserPreview} live />
                        ) : null}
                      </div>
                    ) : null
                  }
                  emptyHint={
                    <EmptyBriefing
                      onCard={openCard}
                      onCardVerb={executeCardVerb}
                      onPrompt={(prompt) => {
                        setSeed(prompt);
                        if (!location.pathname.startsWith('/agent/chat')) navigate('/agent/chat');
                      }}
                    />
                  }
                />
              </div>
            ) : (
              <div className="w-full space-y-5 py-4">
                <div className="flex items-end justify-between gap-3 pb-1">
                  <div>
                    <h1 className="text-[26px] font-semibold tracking-[-0.03em] text-[var(--text)]">
                      Projects
                    </h1>
                    <p className="mt-1 text-[13px] text-[var(--text-muted)]">
                      Scoped workspaces with isolated directories and standing instructions for your unified Vigil stream.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setCreatingProject((prev) => !prev);
                      setSelectedProjectId(null);
                    }}
                    className="inline-flex h-9 items-center gap-1.5 rounded-full bg-[var(--btn-primary-bg)] px-3.5 text-[12.5px] font-semibold text-[var(--btn-primary-ink)] transition-transform hover:scale-105 active:scale-95"
                  >
                    <FolderPlus size={14} strokeWidth={1.75} />
                    New project
                  </button>
                </div>

                {creatingProject ? (
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      const trimmed = newProjectName.trim();
                      if (!trimmed || createProjectMutation.isPending) return;
                      createProjectMutation.mutate(
                        {
                          body: {
                            name: trimmed,
                            description: newProjectDescription.trim() || null,
                            instructions: newProjectInstructions.trim() || null,
                          },
                          scope: `projects:create:${Date.now()}`,
                        },
                        {
                          onSuccess: (created) => {
                            setCreatingProject(false);
                            setNewProjectName('');
                            setNewProjectDescription('');
                            setNewProjectInstructions('');
                            setSelectedProjectId(created.id);
                            setActiveProjectId(created.id);
                          },
                        },
                      );
                    }}
                    className="rail-card space-y-3 p-4"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-[13px] font-semibold text-[var(--text)]">
                        Create Project Workspace
                      </span>
                      <button
                        type="button"
                        onClick={() => setCreatingProject(false)}
                        className="rounded-full p-1 text-[var(--text-muted)] hover:text-[var(--text)]"
                        aria-label="Cancel project creation"
                      >
                        <X size={14} />
                      </button>
                    </div>
                    <input
                      type="text"
                      value={newProjectName}
                      onChange={(e) => setNewProjectName(e.target.value)}
                      placeholder="Project name (e.g. Q4 Launch, Vigil Core)"
                      required
                      className="w-full rounded-[12px] border border-[var(--border)] bg-[var(--panel-inset)] px-3 py-2 text-[13px] text-[var(--text)] placeholder:text-[var(--text-muted)] focus:border-[var(--border-strong)] focus:outline-none"
                    />
                    <input
                      type="text"
                      value={newProjectDescription}
                      onChange={(e) => setNewProjectDescription(e.target.value)}
                      placeholder="Short description (optional)"
                      className="w-full rounded-[12px] border border-[var(--border)] bg-[var(--panel-inset)] px-3 py-2 text-[13px] text-[var(--text)] placeholder:text-[var(--text-muted)] focus:border-[var(--border-strong)] focus:outline-none"
                    />
                    <textarea
                      value={newProjectInstructions}
                      onChange={(e) => setNewProjectInstructions(e.target.value)}
                      placeholder="Standing project instructions injected into runs (optional)"
                      rows={3}
                      className="w-full rounded-[12px] border border-[var(--border)] bg-[var(--panel-inset)] px-3 py-2 text-[13px] text-[var(--text)] placeholder:text-[var(--text-muted)] focus:border-[var(--border-strong)] focus:outline-none"
                    />
                    <div className="flex justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => setCreatingProject(false)}
                        className="rounded-full border border-[var(--border)] px-3.5 py-1.5 text-[12px] font-medium text-[var(--text-muted)] hover:text-[var(--text)]"
                      >
                        Cancel
                      </button>
                      <button
                        type="submit"
                        disabled={!newProjectName.trim() || createProjectMutation.isPending}
                        className="rounded-full bg-[var(--btn-primary-bg)] px-4 py-1.5 text-[12px] font-semibold text-[var(--btn-primary-ink)] disabled:opacity-50"
                      >
                        {createProjectMutation.isPending ? 'Creating...' : 'Create workspace'}
                      </button>
                    </div>
                  </form>
                ) : null}

                {projectsQuery.isLoading ? (
                  <div className="space-y-2">
                    {[0, 1].map((idx) => (
                      <div
                        key={idx}
                        className="h-[74px] animate-pulse rounded-[18px] border border-[var(--border)] bg-[var(--card)] motion-reduce:animate-none"
                      />
                    ))}
                  </div>
                ) : (projectsQuery.data ?? []).length === 0 ? (
                  <div className="rail-card flex flex-col items-center px-6 py-12 text-center">
                    <FolderKanban size={20} strokeWidth={1.5} className="text-[var(--text-muted)]" />
                    <p className="mt-3 text-[14px] font-medium text-[var(--text)]">
                      No project workspaces yet.
                    </p>
                    <p className="mt-1 max-w-sm text-[12.5px] leading-relaxed text-[var(--text-muted)]">
                      All conversation lives in your single primary stream. Create a project workspace when you want isolated files and standing instructions.
                    </p>
                  </div>
                ) : (
                  <div className="space-y-2.5">
                    {(projectsQuery.data ?? []).map((project) => {
                      const isScoped = project.id === activeProjectId;
                      const isSelected = project.id === selectedProjectId;
                      return (
                        <div
                          key={project.id}
                          className={`rounded-[18px] border transition-all ${
                            isScoped || isSelected
                              ? 'border-[var(--text)]/30 bg-[var(--card-strong)]'
                              : 'border-[var(--border)] bg-[var(--card)]'
                          }`}
                        >
                          <div className="flex items-center justify-between gap-3 p-3.5">
                            <button
                              type="button"
                              onClick={() =>
                                setSelectedProjectId((prev) =>
                                  prev === project.id ? null : project.id,
                                )
                              }
                              className="flex min-w-0 flex-1 items-center gap-3 text-left"
                            >
                              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[var(--panel-inset)] text-[var(--text)]">
                                <FolderKanban size={16} strokeWidth={1.6} />
                              </div>
                              <div className="min-w-0">
                                <div className="flex items-center gap-2">
                                  <span className="truncate text-[13.5px] font-semibold text-[var(--text)]">
                                    {project.name}
                                  </span>
                                  {isScoped ? (
                                    <span className="rounded-full bg-[var(--btn-primary-bg)] px-2 py-0.5 text-[10px] font-semibold text-[var(--btn-primary-ink)]">
                                      Active
                                    </span>
                                  ) : null}
                                </div>
                                <div className="mt-0.5 truncate text-[11.5px] text-[var(--text-muted)]">
                                  {project.description || project.workspace_path}
                                </div>
                              </div>
                            </button>
                            <div className="flex shrink-0 items-center gap-1.5">
                              <button
                                type="button"
                                onClick={() => {
                                  setActiveProjectId(isScoped ? null : project.id);
                                  if (!isScoped) {
                                    setActiveTab('chat');
                                  }
                                }}
                                className="inline-flex h-8 items-center gap-1 rounded-full border border-[var(--border)] bg-[var(--panel-bg)] px-3 text-[11.5px] font-medium text-[var(--text)] transition-colors hover:border-[var(--border-strong)]"
                              >
                                <MessageSquare size={12} />
                                {isScoped ? 'Clear scope' : 'Use in stream'}
                              </button>
                            </div>
                          </div>

                          {isSelected ? (
                            <div className="space-y-3 border-t border-[var(--border)] px-4 py-3.5 text-[12.5px]">
                              <div className="flex items-center justify-between gap-2 text-[11px] text-[var(--text-muted)]">
                                <span className="truncate font-mono">{project.workspace_path}</span>
                                <button
                                  type="button"
                                  onClick={() => {
                                    deleteProjectMutation.mutate(
                                      {
                                        projectId: project.id,
                                        scope: `projects:delete:${project.id}`,
                                      },
                                      {
                                        onSuccess: () => {
                                          if (activeProjectId === project.id) {
                                            setActiveProjectId(null);
                                          }
                                          setSelectedProjectId(null);
                                        },
                                      },
                                    );
                                  }}
                                  className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-rose-500 hover:bg-rose-500/10"
                                >
                                  <Trash2 size={12} />
                                  Delete
                                </button>
                              </div>

                              <div className="space-y-2">
                                <label className="block text-[11px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                                  Description
                                </label>
                                <input
                                  type="text"
                                  value={editDescription}
                                  onChange={(e) => setEditDescription(e.target.value)}
                                  className="w-full rounded-[10px] border border-[var(--border)] bg-[var(--panel-inset)] px-2.5 py-1.5 text-[12.5px] text-[var(--text)] focus:border-[var(--border-strong)] focus:outline-none"
                                />
                                <label className="block text-[11px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                                  Standing Instructions
                                </label>
                                <textarea
                                  value={editInstructions}
                                  onChange={(e) => setEditInstructions(e.target.value)}
                                  rows={3}
                                  className="w-full rounded-[10px] border border-[var(--border)] bg-[var(--panel-inset)] px-2.5 py-1.5 text-[12.5px] text-[var(--text)] focus:border-[var(--border-strong)] focus:outline-none"
                                />
                                <div className="flex justify-end">
                                  <button
                                    type="button"
                                    disabled={updateProjectMutation.isPending}
                                    onClick={() =>
                                      updateProjectMutation.mutate({
                                        projectId: project.id,
                                        body: {
                                          description: editDescription.trim() || null,
                                          instructions: editInstructions.trim() || null,
                                        },
                                        scope: `projects:update:${project.id}:${Date.now()}`,
                                      })
                                    }
                                    className="rounded-full bg-[var(--btn-primary-bg)] px-3 py-1 text-[11.5px] font-semibold text-[var(--btn-primary-ink)] disabled:opacity-50"
                                  >
                                    {updateProjectMutation.isPending ? 'Saving...' : 'Save changes'}
                                  </button>
                                </div>
                              </div>

                              <div>
                                <div className="mb-1.5 flex items-center justify-between gap-2">
                                  <div className="text-[11px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                                    Workspace Files
                                  </div>
                                  <div>
                                    <input
                                      ref={projectFileInputRef}
                                      type="file"
                                      multiple
                                      hidden
                                      onChange={(e) => {
                                        const selected = Array.from(e.target.files ?? []);
                                        if (selected.length > 0) {
                                          uploadProjectFilesMutation.mutate({
                                            projectId: project.id,
                                            files: selected,
                                          });
                                        }
                                        e.target.value = '';
                                      }}
                                    />
                                    <button
                                      type="button"
                                      disabled={uploadProjectFilesMutation.isPending}
                                      onClick={() => projectFileInputRef.current?.click()}
                                      className="inline-flex items-center gap-1 rounded-full border border-[var(--border)] bg-[var(--panel-bg)] px-2.5 py-1 text-[11px] font-medium text-[var(--text)] transition-colors hover:border-[var(--border-strong)] disabled:opacity-50"
                                    >
                                      <Upload size={11} />
                                      {uploadProjectFilesMutation.isPending
                                        ? 'Uploading...'
                                        : 'Upload files'}
                                    </button>
                                  </div>
                                </div>
                                {projectDetailQuery.isLoading ? (
                                  <div className="text-[12px] text-[var(--text-muted)]">
                                    Scanning workspace...
                                  </div>
                                ) : (projectDetailQuery.data?.files ?? []).length === 0 ? (
                                  <div className="text-[12px] text-[var(--text-muted)]">
                                    No files in this project workspace yet.
                                  </div>
                                ) : (
                                  <ul className="space-y-1">
                                    {(projectDetailQuery.data?.files ?? []).map((f) => (
                                      <li
                                        key={f.path}
                                        className="flex items-center justify-between rounded-[8px] bg-[var(--panel-inset)] px-2.5 py-1 font-mono text-[11.5px] text-[var(--text)]"
                                      >
                                        <span className="flex items-center gap-1.5 truncate">
                                          <FileText size={12} className="shrink-0 text-[var(--text-muted)]" />
                                          <span className="truncate">{f.path}</span>
                                        </span>
                                        <span className="shrink-0 text-[10.5px] text-[var(--text-muted)]">
                                          {f.size_bytes} B
                                        </span>
                                      </li>
                                    ))}
                                  </ul>
                                )}
                              </div>
                            </div>
                          ) : null}
                        </div>
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
          {activeProject ? (
            <div className="mb-2 flex items-center justify-center">
              <div className="inline-flex items-center gap-2 rounded-full border border-[var(--border)] bg-[var(--panel-bg)] px-3 py-1 text-[11.5px] text-[var(--text)] shadow-[var(--shadow)] backdrop-blur-md">
                <FolderKanban size={12} className="text-[var(--text-muted)]" />
                <span>
                  Workspace: <strong>{activeProject.name}</strong>
                </span>
                <button
                  type="button"
                  onClick={() => setActiveProjectId(null)}
                  className="rounded-full p-0.5 text-[var(--text-muted)] hover:text-[var(--text)]"
                  aria-label="Clear active project workspace"
                >
                  <X size={12} />
                </button>
              </div>
            </div>
          ) : null}
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
