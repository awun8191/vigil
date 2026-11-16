import { useEffect, useState } from 'react';
import {
  AlertCircle,
  ExternalLink,
  Eye,
  Globe,
  Hand,
  Maximize2,
  MousePointerClick,
  X,
} from 'lucide-react';
import { api } from '@/lib/api';
import type { Artifact, RunStepEvent } from '@/types';

export interface BrowserActionStep {
  id: string;
  toolName: string;
  action: string;
  label: string;
  url: string;
  status: 'running' | 'error' | 'success';
  screenshotArtifactId: string | null;
}

export interface BrowserPreview {
  id: string;
  url: string;
  title: string | null;
  status: 'running' | 'error' | 'success';
  statusText: string;
  action: string;
  target: string | null;
  driver: string | null;
  elementCount: number;
  liveViewUrl: string | null;
  interactiveLiveViewUrl: string | null;
  expiresAt: string | null;
  screenshotArtifactId: string | null;
  error: string | null;
  history: BrowserActionStep[];
}

const BROWSER_TOOL_NAMES = new Set([
  'browser_navigate',
  'browser_snapshot',
  'browser_click',
  'browser_type',
  'browser_scroll',
  'browser_press_key',
  'browser_cua_task',
]);

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

function httpsUrl(value: string | null): string | null {
  return value && /^https:\/\//i.test(value) ? value : null;
}

function formatActionLabel(toolName: string, args: Record<string, unknown>): string {
  if (toolName === 'browser_navigate') {
    const url = text(args.url);
    return url ? `Open ${hostOf(url)}` : 'Navigate';
  }
  if (toolName === 'browser_click') {
    const sel = text(args.selector);
    if (sel) return `Click ${sel}`;
    if (typeof args.x === 'number' && typeof args.y === 'number') {
      return `Click (${Math.round(args.x)}, ${Math.round(args.y)})`;
    }
    return 'Click';
  }
  if (toolName === 'browser_type') {
    const val = text(args.text);
    const preview = val ? `"${val.slice(0, 18)}${val.length > 18 ? '…' : ''}"` : 'text';
    return `Type ${preview}`;
  }
  if (toolName === 'browser_scroll') {
    return `Scroll ${text(args.direction) ?? 'down'}`;
  }
  if (toolName === 'browser_press_key') {
    return `Key ${text(args.key) ?? ''}`.trim();
  }
  if (toolName === 'browser_snapshot') return 'Snapshot';
  if (toolName === 'browser_cua_task') {
    const goal = text(args.goal);
    return goal ? `CUA: ${goal.slice(0, 24)}` : 'CUA Task';
  }
  return toolName.replace(/^browser_/, '');
}

/**
 * Pair CUA browser tool calls with their results into a unified live browser
 * console card per run, keeping an action timeline so users can inspect any
 * intermediate screenshot or take interactive control of the live browser.
 */
export function buildBrowserPreviews(
  steps: RunStepEvent[],
  artifacts: Artifact[] = [],
): BrowserPreview[] {
  const artifactByPath = new Map(artifacts.map((artifact) => [artifact.path, artifact.id]));
  const items: BrowserPreview[] = [];
  const byCallId = new Map<string, BrowserPreview>();
  let currentConsole: BrowserPreview | null = null;

  for (const step of steps) {
    const toolName = text(step.payload.tool_name);

    if (step.event_type === 'tool_call') {
      if (!toolName || !BROWSER_TOOL_NAMES.has(toolName)) continue;
      const args = record(step.payload.arguments) ?? {};
      const id = text(step.payload.tool_call_id) ?? `browser-${step.step_number}`;
      const actionLabel = formatActionLabel(toolName, args);
      const url: string = text(args.url) ?? currentConsole?.url ?? '';

      if (!currentConsole) {
        currentConsole = {
          id,
          url,
          title: null,
          status: 'running',
          statusText: 'Running',
          action: toolName.replace(/^browser_/, ''),
          target: text(args.selector) ?? null,
          driver: null,
          elementCount: 0,
          liveViewUrl: null,
          interactiveLiveViewUrl: null,
          expiresAt: null,
          screenshotArtifactId: null,
          error: null,
          history: [],
        };
        items.push(currentConsole);
      } else {
        currentConsole.status = 'running';
        currentConsole.statusText = 'Running';
        currentConsole.action = toolName.replace(/^browser_/, '');
        currentConsole.target = text(args.selector) ?? currentConsole.target;
        if (text(args.url)) currentConsole.url = text(args.url)!;
      }

      currentConsole.history.push({
        id,
        toolName,
        action: toolName.replace(/^browser_/, ''),
        label: actionLabel,
        url,
        status: 'running',
        screenshotArtifactId: null,
      });
      byCallId.set(id, currentConsole);
      continue;
    }

    if (step.event_type !== 'tool_result') continue;
    if (!toolName || !BROWSER_TOOL_NAMES.has(toolName)) continue;

    const callId = text(step.payload.tool_call_id);
    const preview =
      (callId ? byCallId.get(callId) : undefined) ?? currentConsole ?? items[items.length - 1];
    if (!preview) continue;

    const result = record(step.payload.result);
    const content = result ? record(result.content) : null;
    const ok = result?.ok === true;

    preview.status = ok ? 'success' : 'error';
    preview.statusText = ok ? 'Live' : 'Error';
    preview.url = text(content?.url) ?? preview.url;
    preview.title = text(content?.title) ?? preview.title;
    preview.driver = text(content?.driver) ?? preview.driver;
    if (Array.isArray(content?.elements)) {
      preview.elementCount = content.elements.length;
    }
    preview.liveViewUrl = text(content?.live_view_url) ?? preview.liveViewUrl;
    preview.interactiveLiveViewUrl =
      text(content?.interactive_live_view_url) ?? preview.interactiveLiveViewUrl;
    preview.expiresAt = text(content?.session_expires_at) ?? preview.expiresAt;
    if (!ok) {
      preview.error =
        text(result?.error) ?? text(content?.error) ?? 'The browser step failed.';
    } else {
      preview.error = null;
    }

    const path = text(content?.path) ?? text(result?.artifact_ref);
    const artifactId =
      text(content?.artifact_id) ??
      text(result?.artifact_id) ??
      (path ? artifactByPath.get(path) : undefined) ??
      null;
    if (artifactId) preview.screenshotArtifactId = artifactId;

    const historyEntry =
      (callId ? preview.history.find((h) => h.id === callId) : undefined) ??
      preview.history[preview.history.length - 1];
    if (historyEntry) {
      historyEntry.status = ok ? 'success' : 'error';
      historyEntry.url = preview.url;
      if (artifactId) historyEntry.screenshotArtifactId = artifactId;
    }
  }

  for (const preview of items) {
    if (preview.status === 'success' && !preview.liveViewUrl) {
      preview.statusText = preview.driver === 'cdp' ? 'CUA Live' : 'Snapshot';
    }
  }

  return items.filter(
    (preview) => preview.url || preview.screenshotArtifactId || preview.status === 'error',
  );
}

function useArtifactImage(artifactId: string | null) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!artifactId) {
      setUrl(null);
      return undefined;
    }
    let active = true;
    let created: string | null = null;
    api
      .downloadArtifactBlob(artifactId)
      .then((blob) => {
        if (!active) return;
        created = URL.createObjectURL(blob);
        setUrl(created);
      })
      .catch(() => {
        // The live frame or the empty state still explains the page.
      });
    return () => {
      active = false;
      if (created) URL.revokeObjectURL(created);
    };
  }, [artifactId]);

  return url;
}

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function isExpired(expiresAt: string | null): boolean {
  if (!expiresAt) return true;
  const at = Date.parse(expiresAt);
  return Number.isNaN(at) || at <= Date.now();
}

function StatusPill({ status, label }: { status: BrowserPreview['status']; label: string }) {
  if (status === 'error') {
    return (
      <span className="flex h-7 items-center gap-1.5 rounded-full bg-[#DC2626] px-3 text-[11px] font-semibold text-white">
        <AlertCircle size={12} strokeWidth={2} />
        {label}
      </span>
    );
  }
  return (
    <span className="flex h-7 items-center gap-1.5 rounded-full bg-[var(--hark-card-fg)] px-3 text-[11px] font-semibold text-[var(--hark-card-bg)]">
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 rounded-full bg-current ${status === 'running' ? 'animate-pulse' : ''}`}
      />
      {label}
    </span>
  );
}

export function LiveBrowserCard({
  preview,
  live = false,
}: {
  preview: BrowserPreview;
  live?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [takeControl, setTakeControl] = useState(false);
  const [selectedStepId, setSelectedStepId] = useState<string | null>(null);

  const selectedStep = selectedStepId
    ? preview.history.find((h) => h.id === selectedStepId) ?? null
    : null;
  const activeArtifactId = selectedStep?.screenshotArtifactId ?? preview.screenshotArtifactId;
  const screenshotUrl = useArtifactImage(activeArtifactId);

  const inlineWatchUrl =
    httpsUrl(preview.liveViewUrl) ?? httpsUrl(preview.interactiveLiveViewUrl);
  const interactiveUrl =
    httpsUrl(preview.interactiveLiveViewUrl) ?? httpsUrl(preview.liveViewUrl);
  const inlineLiveUrl = takeControl ? interactiveUrl : inlineWatchUrl;
  const expandedLiveUrl = takeControl ? interactiveUrl : inlineWatchUrl ?? interactiveUrl;
  const expired = isExpired(preview.expiresAt);
  const showLive = Boolean(live && !expired && inlineLiveUrl && !selectedStep);
  const canTakeControl = Boolean(live && !expired && interactiveUrl);

  useEffect(() => {
    if (!expanded) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setExpanded(false);
    };
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [expanded]);

  const host = hostOf(preview.url);
  const canExpand = Boolean(expandedLiveUrl || screenshotUrl || preview.error);
  const statusLabel =
    preview.status === 'success'
      ? showLive
        ? takeControl
          ? 'Control'
          : 'Live'
        : preview.driver === 'cdp'
          ? 'CUA'
          : 'Snapshot'
      : preview.statusText;

  return (
    <>
      <div className="hark-card my-2.5 w-full max-w-[560px] overflow-hidden rounded-squircle-md">
        <div className="flex h-9 items-center gap-2.5 border-b border-[var(--border)] px-3">
          <div className="flex shrink-0 gap-1.5" aria-hidden="true">
            {[0, 1, 2].map((dot) => (
              <span
                key={dot}
                className="h-2 w-2 rounded-full bg-[color-mix(in_srgb,var(--text)_14%,transparent)]"
              />
            ))}
          </div>
          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-[var(--text-muted)]">
            {preview.url || host}
          </span>
          {preview.elementCount > 0 ? (
            <span className="hidden shrink-0 items-center gap-1 rounded bg-[color-mix(in_srgb,var(--text)_7%,transparent)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text-muted)] sm:inline-flex">
              <MousePointerClick size={10} />
              {preview.elementCount}
            </span>
          ) : null}
          {canTakeControl ? (
            <button
              type="button"
              onClick={() => {
                setSelectedStepId(null);
                setTakeControl((v) => !v);
              }}
              className={`inline-flex shrink-0 cursor-pointer items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold transition-colors ${
                takeControl
                  ? 'bg-emerald-500/20 text-emerald-400'
                  : 'bg-[color-mix(in_srgb,var(--text)_8%,transparent)] text-[var(--text-muted)] hover:text-[var(--text)]'
              }`}
            >
              {takeControl ? <Hand size={10} /> : <Eye size={10} />}
              {takeControl ? 'Interactive' : 'Take Control'}
            </button>
          ) : showLive ? (
            <span className="shrink-0 text-[10px] font-semibold uppercase tracking-[0.08em] text-emerald-400">
              Live
            </span>
          ) : null}
        </div>

        <div className="relative h-[280px] bg-[var(--surface-solid)] sm:h-[340px]">
          {showLive && inlineLiveUrl ? (
            <iframe
              src={inlineLiveUrl}
              title={`Browser preview: ${preview.url}`}
              className="h-full w-full border-0 bg-white"
            />
          ) : screenshotUrl ? (
            <img
              src={screenshotUrl}
              alt={preview.title ? `Browser preview: ${preview.title}` : 'Browser preview'}
              className="h-full w-full object-cover object-top"
            />
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-2 px-8 text-center">
              {preview.status === 'error' ? (
                <>
                  <AlertCircle size={18} strokeWidth={1.7} className="text-[#DC2626]" />
                  <p className="text-[12px] leading-relaxed text-[var(--text-muted)]">
                    {preview.error ?? 'The browser could not open this page.'}
                  </p>
                </>
              ) : (
                <>
                  <Globe size={18} strokeWidth={1.7} className="text-[var(--text-muted)]" />
                  <p className="text-[12px] text-[var(--text-muted)]">
                    {host ? `Opening ${host}...` : 'Opening the browser...'}
                  </p>
                </>
              )}
            </div>
          )}

          <div className="absolute bottom-3 right-3 flex items-center gap-1.5">
            <StatusPill status={preview.status} label={statusLabel} />
            {canExpand ? (
              <button
                type="button"
                onClick={() => setExpanded(true)}
                aria-label="Expand browser preview"
                title="Expand browser preview"
                className="flex h-7 w-7 cursor-pointer items-center justify-center rounded-full bg-[var(--hark-card-fg)] text-[var(--hark-card-bg)] transition-transform hover:scale-105 active:scale-95"
              >
                <Maximize2 size={12} strokeWidth={2} />
              </button>
            ) : null}
          </div>
        </div>

        {preview.history.length > 1 ? (
          <div className="flex items-center gap-1.5 overflow-x-auto border-t border-[var(--border)] px-3 py-2">
            {preview.history.map((step, index) => {
              const isSelected = selectedStepId
                ? selectedStepId === step.id
                : index === preview.history.length - 1;
              return (
                <button
                  key={step.id}
                  type="button"
                  onClick={() =>
                    setSelectedStepId(index === preview.history.length - 1 ? null : step.id)
                  }
                  className={`shrink-0 cursor-pointer rounded-md px-2 py-0.5 font-mono text-[10px] transition-colors ${
                    isSelected
                      ? 'bg-[var(--text)] text-[var(--surface)]'
                      : 'bg-[color-mix(in_srgb,var(--text)_6%,transparent)] text-[var(--text-muted)] hover:text-[var(--text)]'
                  }`}
                >
                  {index + 1}. {step.label}
                </button>
              );
            })}
          </div>
        ) : null}
      </div>

      {expanded ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`Browser preview: ${preview.url}`}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 p-3 backdrop-blur-sm sm:p-6"
          onClick={() => setExpanded(false)}
        >
          <div
            className="hark-card flex h-[min(90dvh,900px)] w-full max-w-6xl flex-col overflow-hidden rounded-squircle-lg"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex h-12 shrink-0 items-center gap-2.5 border-b border-[var(--border)] px-4">
              <Globe size={14} strokeWidth={1.7} className="shrink-0 text-[var(--text-muted)]" />
              <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-[var(--text)]">
                {preview.url || host}
              </span>
              {canTakeControl ? (
                <button
                  type="button"
                  onClick={() => {
                    setSelectedStepId(null);
                    setTakeControl((v) => !v);
                  }}
                  className={`inline-flex shrink-0 cursor-pointer items-center gap-1.5 rounded-full px-3 py-1 text-[11px] font-semibold transition-colors ${
                    takeControl
                      ? 'bg-emerald-500/20 text-emerald-400'
                      : 'bg-[color-mix(in_srgb,var(--text)_8%,transparent)] text-[var(--text-muted)] hover:text-[var(--text)]'
                  }`}
                >
                  {takeControl ? <Hand size={12} /> : <Eye size={12} />}
                  {takeControl ? 'Interactive Control' : 'Take Control'}
                </button>
              ) : null}
              {expandedLiveUrl ? (
                <a
                  href={expandedLiveUrl}
                  target="_blank"
                  rel="noreferrer noopener"
                  aria-label="Open browser view in a new tab"
                  title="Open in new tab"
                  className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[8px] text-[var(--text-muted)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)]"
                >
                  <ExternalLink size={15} strokeWidth={1.7} />
                </a>
              ) : null}
              <button
                type="button"
                onClick={() => setExpanded(false)}
                aria-label="Close browser preview"
                className="inline-flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-[8px] text-[var(--text-muted)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)]"
              >
                <X size={16} strokeWidth={1.7} />
              </button>
            </div>

            <div className="min-h-0 flex-1 bg-[var(--surface-solid)]">
              {!expired && expandedLiveUrl && !selectedStep ? (
                <iframe
                  src={expandedLiveUrl}
                  title={`Browser preview: ${preview.url}`}
                  className="h-full w-full border-0 bg-white"
                />
              ) : screenshotUrl ? (
                <div className="flex h-full items-center justify-center overflow-auto p-4">
                  <img
                    src={screenshotUrl}
                    alt={preview.title ? `Browser preview: ${preview.title}` : 'Browser preview'}
                    className="h-auto max-w-full object-contain"
                  />
                </div>
              ) : (
                <div className="flex h-full flex-col items-center justify-center gap-2 px-8 text-center">
                  <Globe size={20} strokeWidth={1.7} className="text-[var(--text-muted)]" />
                  <p className="text-[13px] text-[var(--text-muted)]">
                    {preview.error ?? 'This browser session has closed.'}
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

export default LiveBrowserCard;
