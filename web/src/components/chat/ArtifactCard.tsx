import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Download,
  Expand,
  ExternalLink,
  FileCode2,
  FileSpreadsheet,
  FileText,
  Image as ImageIcon,
  X,
  ZoomIn,
  ZoomOut,
  ChevronDown,
  ChevronUp,
  Pin,
  Eye,
  Code,
  Smartphone,
  Tablet,
  Monitor,
} from 'lucide-react';
import { api } from '@/lib/api';
import type { Artifact, RunStepEvent } from '@/types';
import { MarkdownContent } from './MarkdownContent';

export const PINNED_WIDGETS_STORAGE_KEY = 'vigil_pinned_widgets';

export interface PinnedWidget {
  id: string;
  artifactId: string | null;
  name: string;
  content: string;
  format: string;
  pinnedAt: string;
}

export function getPinnedWidgets(): PinnedWidget[] {
  try {
    const raw = localStorage.getItem(PINNED_WIDGETS_STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export function savePinnedWidgets(widgets: PinnedWidget[]): void {
  try {
    localStorage.setItem(PINNED_WIDGETS_STORAGE_KEY, JSON.stringify(widgets));
    window.dispatchEvent(new Event('vigil:pinned-widgets-changed'));
  } catch {
    // ignore
  }
}

export function usePinnedWidgets() {
  const [widgets, setWidgets] = useState<PinnedWidget[]>(getPinnedWidgets);

  useEffect(() => {
    const handleUpdate = () => setWidgets(getPinnedWidgets());
    window.addEventListener('vigil:pinned-widgets-changed', handleUpdate);
    window.addEventListener('storage', handleUpdate);
    return () => {
      window.removeEventListener('vigil:pinned-widgets-changed', handleUpdate);
      window.removeEventListener('storage', handleUpdate);
    };
  }, []);

  const pinWidget = (widget: PinnedWidget) => {
    const current = getPinnedWidgets().filter((w) => w.id !== widget.id);
    const updated = [widget, ...current];
    savePinnedWidgets(updated);
    setWidgets(updated);
  };

  const unpinWidget = (id: string) => {
    const updated = getPinnedWidgets().filter((w) => w.id !== id);
    savePinnedWidgets(updated);
    setWidgets(updated);
  };

  const isPinned = (id: string) => widgets.some((w) => w.id === id);

  return { widgets, pinWidget, unpinWidget, isPinned };
}

export interface DeliverableItem {
  id: string;
  artifactId: string | null;
  name: string;
  path: string;
  sizeBytes: number;
  sha256: string | null;
  preview: string | null;
  format: 'markdown' | 'pdf' | 'docx' | 'xlsx' | 'csv' | 'image' | 'html' | 'text';
}

function detectDeliverableFormat(name: string, rawFormat?: string | null): DeliverableItem['format'] {
  const ext = name.includes('.') ? (name.split('.').pop()?.toLowerCase() ?? '') : '';
  const fmt = (rawFormat ?? '').toLowerCase();
  if (ext === 'md' || ext === 'markdown' || fmt === 'markdown') return 'markdown';
  if (ext === 'pdf' || fmt === 'pdf') return 'pdf';
  if (ext === 'docx' || fmt === 'docx') return 'docx';
  if (ext === 'xlsx' || ext === 'xlsm' || fmt === 'xlsx') return 'xlsx';
  if (ext === 'csv' || fmt === 'csv') return 'csv';
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'ico'].includes(ext) || fmt === 'image' || fmt === 'screenshot') {
    return 'image';
  }
  if (ext === 'html' || ext === 'htm' || fmt === 'html' || fmt === 'micro_app') return 'html';
  return 'text';
}

function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

interface ParsedSheet {
  name: string;
  rows: string[][];
}

function parseCsvLine(line: string, delimiter: string): string[] {
  const cells: string[] = [];
  let current = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (ch === '"') {
      if (inQuotes && line[i + 1] === '"') {
        current += '"';
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
    } else if (ch === delimiter && !inQuotes) {
      cells.push(current.trim());
      current = '';
    } else {
      current += ch;
    }
  }
  cells.push(current.trim());
  return cells;
}

function parseSpreadsheetPreview(text: string, format: 'xlsx' | 'csv'): ParsedSheet[] {
  const trimmed = text.trim();
  if (!trimmed) return [];

  if (format === 'xlsx' && trimmed.includes('[sheet:')) {
    const blocks = trimmed.split(/(?=\[sheet:\s*[^\]]+\])/g);
    const sheets: ParsedSheet[] = [];
    for (const block of blocks) {
      const lines = block.trim().split(/\r?\n/);
      if (lines.length === 0) continue;
      const headerMatch = /^\[sheet:\s*([^\]]+)\]$/.exec(lines[0]?.trim() ?? '');
      const sheetName = headerMatch?.[1]?.trim() || 'Sheet1';
      const bodyLines = headerMatch ? lines.slice(1) : lines;
      const rows = bodyLines
        .filter((l) => l.trim().length > 0 && !l.startsWith('... [truncated'))
        .map((l) => (l.includes('\t') ? l.split('\t') : parseCsvLine(l, ',')));
      if (rows.length > 0) {
        sheets.push({ name: sheetName, rows });
      }
    }
    if (sheets.length > 0) return sheets;
  }

  const lines = trimmed.split(/\r?\n/).filter((l) => l.trim().length > 0);
  const delimiter = lines[0]?.includes('\t') ? '\t' : ',';
  const rows = lines.map((l) =>
    delimiter === '\t' ? l.split('\t') : parseCsvLine(l, ','),
  );
  return rows.length > 0 ? [{ name: 'Sheet1', rows }] : [];
}

/**
 * Combines registered artifacts from `GET /artifacts/run/{run_id}` with tool step metadata
 * (`generate_document`, `deliver_file`) so `sha256` and inline previews are always available.
 */
export function buildDeliverables(
  artifacts: Artifact[],
  steps: RunStepEvent[],
): DeliverableItem[] {
  const stepMetaByPath = new Map<
    string,
    { name?: string; sha256?: string; sizeBytes?: number; format?: string; preview?: string }
  >();

  for (let i = 0; i < steps.length; i += 1) {
    const step = steps[i];
    if (!step) continue;
    if (step.event_type === 'tool_result') {
      const toolName = String(step.payload.tool_name ?? '');
      if (toolName !== 'generate_document' && toolName !== 'deliver_file') continue;
      const res =
        typeof step.payload.result === 'object' && step.payload.result !== null
          ? (step.payload.result as Record<string, unknown>)
          : null;
      if (!res || res.ok !== true) continue;
      const content =
        typeof res.content === 'object' && res.content !== null
          ? (res.content as Record<string, unknown>)
          : {};
      const path =
        typeof content.path === 'string'
          ? content.path
          : typeof res.artifact_ref === 'string'
            ? res.artifact_ref
            : '';
      if (!path) continue;
      stepMetaByPath.set(path, {
        name: typeof content.name === 'string' ? content.name : undefined,
        sha256: typeof content.sha256 === 'string' ? content.sha256 : undefined,
        sizeBytes: typeof content.size_bytes === 'number' ? content.size_bytes : undefined,
        format: typeof content.format === 'string' ? content.format : undefined,
      });
    } else if (step.event_type === 'tool_call') {
      const toolName = String(step.payload.tool_name ?? '');
      if (toolName !== 'generate_document') continue;
      const args =
        typeof step.payload.arguments === 'object' && step.payload.arguments !== null
          ? (step.payload.arguments as Record<string, unknown>)
          : {};
      const filename = typeof args.filename === 'string' ? args.filename : '';
      const bodyText = typeof args.content === 'string' ? args.content : '';
      if (filename && bodyText) {
        stepMetaByPath.set(`name:${filename}`, {
          name: filename,
          preview: bodyText,
          format: typeof args.format === 'string' ? args.format : undefined,
        });
      }
    }
  }

  const items: DeliverableItem[] = [];
  const seenPaths = new Set<string>();

  for (const art of artifacts) {
    seenPaths.add(art.path);
    const baseName = art.path.split('/').pop() ?? art.path;
    const stepMeta = stepMetaByPath.get(art.path) ?? stepMetaByPath.get(`name:${baseName}`);
    const name = stepMeta?.name ?? baseName;
    const format = detectDeliverableFormat(name, stepMeta?.format);
    items.push({
      id: art.id,
      artifactId: art.id,
      name,
      path: art.path,
      sizeBytes: art.size_bytes || stepMeta?.sizeBytes || 0,
      sha256: stepMeta?.sha256 ?? null,
      preview: art.preview ?? stepMeta?.preview ?? null,
      format,
    });
  }

  for (const [pathKey, meta] of stepMetaByPath.entries()) {
    if (pathKey.startsWith('name:') || seenPaths.has(pathKey)) continue;
    const baseName = meta.name ?? pathKey.split('/').pop() ?? pathKey;
    const nameMeta = stepMetaByPath.get(`name:${baseName}`);
    const format = detectDeliverableFormat(baseName, meta.format ?? nameMeta?.format);
    items.push({
      id: `step-${pathKey}`,
      artifactId: null,
      name: baseName,
      path: pathKey,
      sizeBytes: meta.sizeBytes ?? 0,
      sha256: meta.sha256 ?? null,
      preview: meta.preview ?? nameMeta?.preview ?? null,
      format,
    });
  }

  return items;
}

function FormatIcon({ format }: { format: DeliverableItem['format'] }) {
  switch (format) {
    case 'xlsx':
    case 'csv':
      return <FileSpreadsheet size={15} strokeWidth={1.65} className="shrink-0 text-emerald-400" />;
    case 'image':
      return <ImageIcon size={15} strokeWidth={1.65} className="shrink-0 text-amber-400" />;
    case 'html':
      return <FileCode2 size={15} strokeWidth={1.65} className="shrink-0 text-[var(--text-muted)]" />;
    case 'pdf':
      return <FileText size={15} strokeWidth={1.65} className="shrink-0 text-rose-400" />;
    case 'docx':
      return <FileText size={15} strokeWidth={1.65} className="shrink-0 text-[var(--text-muted)]" />;
    default:
      return <FileText size={15} strokeWidth={1.65} className="shrink-0 text-[var(--text)]" />;
  }
}

function ViewerIconButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className="inline-flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-[8px] text-[var(--text-muted)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)] active:scale-95"
    >
      {children}
    </button>
  );
}

function ViewerSegment({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`inline-flex cursor-pointer items-center gap-1 rounded-[7px] px-2.5 py-1 text-[11px] font-medium transition-colors ${
        active
          ? 'bg-[var(--hark-card-fg)] text-[var(--hark-card-bg)]'
          : 'text-[var(--text-muted)] hover:text-[var(--text)]'
      }`}
    >
      {children}
    </button>
  );
}

export function ArtifactCard({ item }: { item: DeliverableItem }) {
  const [expandedInline, setExpandedInline] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [fullText, setFullText] = useState<string | null>(item.preview);
  const [loadingBlob, setLoadingBlob] = useState(false);
  const [activeSheet, setActiveSheet] = useState(0);
  const [zoom, setZoom] = useState(100);
  const [viewMode, setViewMode] = useState<'preview' | 'code'>('preview');
  const [viewportWidth, setViewportWidth] = useState<'375px' | '768px' | '100%'>('100%');

  const { pinWidget, unpinWidget, isPinned } = usePinnedWidgets();
  const pinned = isPinned(item.id);

  const handleTogglePin = () => {
    if (pinned) {
      unpinWidget(item.id);
    } else {
      pinWidget({
        id: item.id,
        artifactId: item.artifactId,
        name: item.name,
        content: fullText ?? item.preview ?? '',
        format: item.format,
        pinnedAt: new Date().toISOString(),
      });
    }
  };

  const needsBlobForInline = item.format === 'image' || item.format === 'pdf';
  const isTextFormat =
    item.format === 'markdown' ||
    item.format === 'csv' ||
    item.format === 'text' ||
    item.format === 'html' ||
    item.format === 'docx' ||
    item.format === 'xlsx';

  useEffect(() => {
    if (!item.artifactId) return;
    const artifactId = item.artifactId;
    let active = true;
    let createdUrl: string | null = null;
    setLoadingBlob(true);

    void (async () => {
      try {
        if (needsBlobForInline) {
          const blob = await api.downloadArtifactBlob(artifactId);
          if (!active) return;
          createdUrl = URL.createObjectURL(blob);
          setBlobUrl(createdUrl);
        }
        if (isTextFormat) {
          const text = await api.getArtifactText(artifactId);
          if (active && text) setFullText(text);
        }
      } catch {
        // Keep the registered preview when the file is unreachable
      } finally {
        if (active) setLoadingBlob(false);
      }
    })();

    return () => {
      active = false;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [item.artifactId, item.format, needsBlobForInline, isTextFormat]);

  useEffect(() => {
    if (!modalOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setModalOpen(false);
    };
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [modalOpen]);

  const handleOpenExternal = () => {
    if (blobUrl) window.open(blobUrl, '_blank', 'noopener,noreferrer');
  };

  const handleDownload = async () => {
    try {
      let url = blobUrl;
      let revokeAfter = false;
      if (!url) {
        if (item.artifactId) {
          const blob = await api.downloadArtifactBlob(item.artifactId);
          url = URL.createObjectURL(blob);
          revokeAfter = true;
        } else if (fullText) {
          const blob = new Blob([fullText], { type: 'text/plain;charset=utf-8' });
          url = URL.createObjectURL(blob);
          revokeAfter = true;
        }
      }
      if (!url) return;
      const a = document.createElement('a');
      a.href = url;
      a.download = item.name;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      if (revokeAfter) setTimeout(() => URL.revokeObjectURL(url as string), 1500);
    } catch {
      // Ignore download failure
    }
  };

  const sheets = useMemo(() => {
    if (item.format !== 'xlsx' && item.format !== 'csv') return [];
    return parseSpreadsheetPreview(fullText ?? item.preview ?? '', item.format);
  }, [item.format, fullText, item.preview]);

  const currentSheet = sheets[activeSheet] ?? sheets[0];
  const shortSha = item.sha256 ? item.sha256.slice(0, 8) : null;
  const canOpenExternal = item.format === 'image' || item.format === 'pdf';
  const documentText = fullText ?? item.preview ?? '';

  const documentParagraphs = useMemo(() => {
    if (item.format !== 'docx') return [];
    return documentText
      .split(/\n{2,}/)
      .map((paragraph) => paragraph.trim())
      .filter(Boolean);
  }, [item.format, documentText]);

  return (
    <>
      <div className="hark-card my-2.5 w-full max-w-full min-w-0 overflow-hidden rounded-squircle-md p-1">
        <div className="overflow-hidden rounded-squircle-sm border border-[var(--border)] bg-[color-mix(in_srgb,var(--hark-card-fg)_4%,var(--hark-card-bg))]">
          {/* Card Header */}
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border)] px-4 py-3">
            <div className="flex min-w-0 items-center gap-2.5">
              <FormatIcon format={item.format} />
              <span className="truncate font-mono text-[12px] font-semibold text-[var(--text)]">
                {item.name}
              </span>
              <span className="rounded-full border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_6%,transparent)] px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.06em] text-[var(--text-muted)]">
                {item.format}
              </span>
              {item.sizeBytes > 0 ? (
                <span className="font-mono text-[11px] tabular-nums text-[var(--text-muted)]">
                  {formatFileSize(item.sizeBytes)}
                </span>
              ) : null}
              {shortSha ? (
                <span
                  title={`SHA-256: ${item.sha256}`}
                  className="hidden sm:inline-block rounded-[5px] bg-[color-mix(in_srgb,var(--text)_6%,transparent)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text-muted)]"
                >
                  #{shortSha}
                </span>
              ) : null}
            </div>

            <div className="flex shrink-0 items-center gap-1.5">
              {(item.format === 'markdown' || item.format === 'text' || item.format === 'docx') &&
              (fullText ?? item.preview) ? (
                <button
                  type="button"
                  onClick={() => setExpandedInline((v) => !v)}
                  className="inline-flex cursor-pointer items-center gap-1 rounded-[7px] border border-transparent px-2 py-1 text-[11px] font-medium text-[var(--text-muted)] transition-colors hover:border-[var(--border)] hover:bg-[color-mix(in_srgb,var(--text)_7%,transparent)] hover:text-[var(--text)]"
                >
                  {expandedInline ? (
                    <>
                      <ChevronUp size={12} strokeWidth={1.75} />
                      <span>Collapse</span>
                    </>
                  ) : (
                    <>
                      <ChevronDown size={12} strokeWidth={1.75} />
                      <span>Expand</span>
                    </>
                  )}
                </button>
              ) : null}

              <button
                type="button"
                onClick={() => setModalOpen(true)}
                aria-label={`Open ${item.name} in full viewer`}
                className="inline-flex cursor-pointer items-center gap-1 rounded-[7px] border border-transparent px-2 py-1 text-[11px] font-medium text-[var(--text-muted)] transition-colors hover:border-[var(--border)] hover:bg-[color-mix(in_srgb,var(--text)_7%,transparent)] hover:text-[var(--text)]"
              >
                <Expand size={12} strokeWidth={1.75} />
                <span>Full view</span>
              </button>

              <button
                type="button"
                onClick={handleTogglePin}
                aria-label={pinned ? `Unpin ${item.name} from dashboard` : `Pin ${item.name} to dashboard`}
                title={pinned ? 'Unpin from dashboard' : 'Pin to dashboard'}
                className={`inline-flex cursor-pointer items-center gap-1.5 rounded-[7px] border px-2.5 py-1 text-[11px] font-medium transition-colors ${
                  pinned
                    ? 'border-[var(--hark-card-border)] bg-[var(--hark-control-bg)] text-[var(--hark-control-fg)]'
                    : 'border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_6%,transparent)] text-[var(--text-muted)] hover:text-[var(--text)]'
                }`}
              >
                <Pin size={12} strokeWidth={1.75} className={pinned ? 'fill-current' : ''} />
                <span>{pinned ? 'Pinned' : 'Pin'}</span>
              </button>

              <button
                type="button"
                onClick={handleDownload}
                aria-label={`Download ${item.name}`}
                className="inline-flex cursor-pointer items-center gap-1.5 rounded-[7px] border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_6%,transparent)] px-2.5 py-1 text-[11px] font-medium text-[var(--text)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_12%,transparent)] active:scale-95"
              >
                <Download size={12} strokeWidth={1.75} />
                <span>Download</span>
              </button>
            </div>
          </div>

          {/* Card Body by Format */}
          <div className="p-3.5">
            {item.format === 'image' ? (
              <div className="flex flex-col items-center justify-center overflow-hidden rounded-[8px] border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_2%,transparent)] p-2">
                {blobUrl ? (
                  <button
                    type="button"
                    onClick={() => setModalOpen(true)}
                    className="group/img relative block max-h-[340px] cursor-zoom-in overflow-hidden rounded-[6px]"
                  >
                    <img
                      src={blobUrl}
                      alt={item.name}
                      className="max-h-[320px] w-auto max-w-full object-contain transition-transform duration-200 group-hover/img:scale-[1.01]"
                    />
                  </button>
                ) : (
                  <div className="flex h-32 w-full items-center justify-center text-[12px] text-[var(--text-muted)]">
                    {loadingBlob ? 'Loading image preview...' : 'Image ready for download'}
                  </div>
                )}
              </div>
            ) : item.format === 'pdf' ? (
              <div className="overflow-hidden rounded-[8px] border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_2%,transparent)]">
                {blobUrl ? (
                  <iframe
                    src={blobUrl}
                    title={item.name}
                    className="h-[340px] w-full border-0"
                  />
                ) : item.preview ? (
                  <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap font-mono text-[12px] leading-[1.6] text-[var(--text-muted)]">
                    {item.preview}
                  </pre>
                ) : (
                  <div className="flex h-28 items-center justify-center text-[12px] text-[var(--text-muted)]">
                    {loadingBlob ? 'Loading PDF preview...' : 'PDF document ready'}
                  </div>
                )}
              </div>
            ) : item.format === 'xlsx' || item.format === 'csv' ? (
              <div className="space-y-2">
                {sheets.length > 1 ? (
                  <div className="flex flex-wrap gap-1 border-b border-[var(--border)] pb-2">
                    {sheets.map((sheet, idx) => (
                      <button
                        key={sheet.name}
                        type="button"
                        onClick={() => setActiveSheet(idx)}
                        className={`cursor-pointer rounded-[6px] px-2.5 py-1 font-mono text-[11px] font-medium transition-colors ${
                          idx === activeSheet
                            ? 'bg-[var(--text)] text-[var(--bg)]'
                            : 'text-[var(--text-muted)] hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)]'
                        }`}
                      >
                        {sheet.name}
                      </button>
                    ))}
                  </div>
                ) : null}

                {currentSheet && currentSheet.rows.length > 0 ? (
                  <div className="scroll-soft max-h-[280px] overflow-auto rounded-[8px] border border-[var(--border)]">
                    <table className="w-full border-collapse text-left font-mono text-[12px]">
                      <thead className="sticky top-0 bg-[color-mix(in_srgb,var(--surface-solid)_95%,transparent)] text-[var(--text)] shadow-xs">
                        <tr>
                          {(currentSheet.rows[0] ?? []).map((cell, cIdx) => (
                            <th
                              key={cIdx}
                              className="border-b border-r border-[var(--border)] last:border-r-0 px-3 py-1.5 font-semibold whitespace-nowrap"
                            >
                              {cell || `Col ${cIdx + 1}`}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-[var(--border)]">
                        {currentSheet.rows.slice(1, 21).map((row, rIdx) => (
                          <tr
                            key={rIdx}
                            className="hover:bg-[color-mix(in_srgb,var(--text)_4%,transparent)]"
                          >
                            {row.map((cell, cIdx) => (
                              <td
                                key={cIdx}
                                className="border-r border-[var(--border)] last:border-r-0 px-3 py-1.5 whitespace-nowrap text-[var(--text-muted)]"
                              >
                                {cell}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="text-[12px] text-[var(--text-muted)]">
                    Spreadsheet ready for download.
                  </p>
                )}
              </div>
            ) : item.format === 'html' ? (
              <div className="flex flex-col gap-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-0.5 rounded-[9px] bg-[color-mix(in_srgb,var(--text)_7%,transparent)] p-0.5">
                    <ViewerSegment
                      active={viewMode === 'preview'}
                      onClick={() => setViewMode('preview')}
                    >
                      <Eye size={12} strokeWidth={1.8} />
                      Preview
                    </ViewerSegment>
                    <ViewerSegment active={viewMode === 'code'} onClick={() => setViewMode('code')}>
                      <Code size={12} strokeWidth={1.8} />
                      Code
                    </ViewerSegment>
                  </div>

                  {viewMode === 'preview' ? (
                    <div className="flex items-center gap-0.5 rounded-[9px] bg-[color-mix(in_srgb,var(--text)_7%,transparent)] p-0.5">
                      <ViewerSegment
                        active={viewportWidth === '375px'}
                        onClick={() => setViewportWidth('375px')}
                      >
                        <Smartphone size={11} strokeWidth={1.8} />
                        375
                      </ViewerSegment>
                      <ViewerSegment
                        active={viewportWidth === '768px'}
                        onClick={() => setViewportWidth('768px')}
                      >
                        <Tablet size={11} strokeWidth={1.8} />
                        768
                      </ViewerSegment>
                      <ViewerSegment
                        active={viewportWidth === '100%'}
                        onClick={() => setViewportWidth('100%')}
                      >
                        <Monitor size={11} strokeWidth={1.8} />
                        Full
                      </ViewerSegment>
                    </div>
                  ) : null}
                </div>

                {viewMode === 'preview' ? (
                  <div className="scroll-soft overflow-x-auto rounded-[8px] bg-[color-mix(in_srgb,var(--text)_5%,transparent)]">
                    <iframe
                      sandbox="allow-scripts"
                      srcDoc={documentText}
                      title={item.name}
                      style={{ width: viewportWidth }}
                      className="mx-auto block h-[380px] border-0 bg-white"
                    />
                  </div>
                ) : (
                  <pre className="scroll-soft max-h-[380px] overflow-auto whitespace-pre rounded-[8px] bg-[color-mix(in_srgb,var(--text)_3%,transparent)] p-3 font-mono text-[12px] leading-relaxed text-[var(--text)]">
                    {documentText || 'No HTML content available.'}
                  </pre>
                )}
              </div>
            ) : (
              /* Markdown, DOCX, or Plain Text */
              <div
                className={`relative overflow-hidden transition-all ${
                  expandedInline ? 'max-h-none' : 'max-h-[15.5rem]'
                }`}
              >
                {item.format === 'markdown' ? (
                  <MarkdownContent
                    content={fullText ?? item.preview ?? '*Document ready for download.*'}
                    className="text-[13.5px]"
                  />
                ) : (
                  <pre className="whitespace-pre-wrap font-mono text-[12.5px] leading-[1.65] text-[var(--text)]">
                    {fullText ?? item.preview ?? 'Document ready for download.'}
                  </pre>
                )}
                {!expandedInline && (fullText ?? item.preview ?? '').split('\n').length > 12 ? (
                  <div className="pointer-events-none absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-[var(--surface-solid)] to-transparent" />
                ) : null}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Full-Width Modal Viewer */}
      {modalOpen ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={item.name}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 p-3 backdrop-blur-sm sm:p-6"
          onClick={() => setModalOpen(false)}
        >
          <div
            className="hark-card flex h-[min(92dvh,940px)] w-full max-w-6xl flex-col overflow-hidden rounded-squircle-lg"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header: one line, icon actions only */}
            <div className="flex h-14 shrink-0 items-center gap-2.5 border-b border-[var(--border)] px-4">
              <FormatIcon format={item.format} />
              <div className="flex min-w-0 items-baseline gap-2">
                <span className="truncate text-[13px] font-semibold tracking-[-0.01em] text-[var(--text)]">
                  {item.name}
                </span>
                <span className="shrink-0 font-mono text-[11px] tabular-nums text-[var(--text-muted)]">
                  {formatFileSize(item.sizeBytes)}
                </span>
              </div>

              <div className="ml-auto flex items-center gap-0.5">
                {canOpenExternal ? (
                  <ViewerIconButton label="Open in new tab" onClick={handleOpenExternal}>
                    <ExternalLink size={15} strokeWidth={1.7} />
                  </ViewerIconButton>
                ) : null}
                <ViewerIconButton label={`Download ${item.name}`} onClick={handleDownload}>
                  <Download size={15} strokeWidth={1.7} />
                </ViewerIconButton>
                <ViewerIconButton label="Close viewer" onClick={() => setModalOpen(false)}>
                  <X size={16} strokeWidth={1.7} />
                </ViewerIconButton>
              </div>
            </div>

            {/* Toolbar: only the controls this format needs */}
            {item.format === 'html' || item.format === 'image' ? (
              <div className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-[var(--border)] px-4">
                {item.format === 'html' ? (
                  <div className="flex items-center gap-0.5 rounded-[9px] bg-[color-mix(in_srgb,var(--text)_7%,transparent)] p-0.5">
                    <ViewerSegment
                      active={viewMode === 'preview'}
                      onClick={() => setViewMode('preview')}
                    >
                      <Eye size={12} strokeWidth={1.8} />
                      Preview
                    </ViewerSegment>
                    <ViewerSegment active={viewMode === 'code'} onClick={() => setViewMode('code')}>
                      <Code size={12} strokeWidth={1.8} />
                      Code
                    </ViewerSegment>
                  </div>
                ) : (
                  <span className="text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--text-muted)]">
                    Image
                  </span>
                )}

                {item.format === 'html' && viewMode === 'preview' ? (
                  <div className="flex items-center gap-0.5 rounded-[9px] bg-[color-mix(in_srgb,var(--text)_7%,transparent)] p-0.5">
                    <ViewerSegment
                      active={viewportWidth === '375px'}
                      onClick={() => setViewportWidth('375px')}
                    >
                      <Smartphone size={12} strokeWidth={1.8} />
                      375
                    </ViewerSegment>
                    <ViewerSegment
                      active={viewportWidth === '768px'}
                      onClick={() => setViewportWidth('768px')}
                    >
                      <Tablet size={12} strokeWidth={1.8} />
                      768
                    </ViewerSegment>
                    <ViewerSegment
                      active={viewportWidth === '100%'}
                      onClick={() => setViewportWidth('100%')}
                    >
                      <Monitor size={12} strokeWidth={1.8} />
                      Full
                    </ViewerSegment>
                  </div>
                ) : item.format === 'image' ? (
                  <div className="flex items-center gap-1">
                    <ViewerIconButton
                      label="Zoom out"
                      onClick={() => setZoom((z) => Math.max(50, z - 25))}
                    >
                      <ZoomOut size={14} strokeWidth={1.7} />
                    </ViewerIconButton>
                    <span className="min-w-[2.5rem] text-center font-mono text-[11px] tabular-nums text-[var(--text-muted)]">
                      {zoom}%
                    </span>
                    <ViewerIconButton
                      label="Zoom in"
                      onClick={() => setZoom((z) => Math.min(250, z + 25))}
                    >
                      <ZoomIn size={14} strokeWidth={1.7} />
                    </ViewerIconButton>
                  </div>
                ) : null}
              </div>
            ) : null}

            {/* Viewer body */}
            <div className="min-h-0 flex-1 overflow-hidden">
              {item.format === 'html' ? (
                viewMode === 'preview' ? (
                  <div className="scroll-soft h-full overflow-auto bg-[color-mix(in_srgb,var(--text)_5%,transparent)]">
                    <iframe
                      sandbox="allow-scripts"
                      srcDoc={documentText}
                      title={item.name}
                      style={{ width: viewportWidth }}
                      className="mx-auto block h-full border-0 bg-white"
                    />
                  </div>
                ) : (
                  <pre className="scroll-soft h-full overflow-auto whitespace-pre px-5 py-4 font-mono text-[12.5px] leading-[1.72] text-[var(--text)]">
                    {documentText || 'No HTML content available.'}
                  </pre>
                )
              ) : item.format === 'image' ? (
                <div className="scroll-soft flex h-full items-center justify-center overflow-auto bg-[color-mix(in_srgb,var(--text)_5%,transparent)] p-4">
                  {blobUrl ? (
                    <img
                      src={blobUrl}
                      alt={item.name}
                      style={{ width: `${zoom}%`, maxWidth: 'none' }}
                      className="h-auto object-contain transition-all duration-150"
                    />
                  ) : (
                    <span className="text-[13px] text-[var(--text-muted)]">
                      {loadingBlob ? 'Loading image...' : 'Image preview unavailable.'}
                    </span>
                  )}
                </div>
              ) : item.format === 'pdf' ? (
                blobUrl ? (
                  <iframe
                    src={blobUrl}
                    title={item.name}
                    className="h-full w-full border-0 bg-[var(--surface-solid)]"
                  />
                ) : (
                  <div className="flex h-full items-center justify-center text-[13px] text-[var(--text-muted)]">
                    {loadingBlob ? 'Loading document...' : 'PDF preview unavailable.'}
                  </div>
                )
              ) : item.format === 'xlsx' || item.format === 'csv' ? (
                <div className="flex h-full flex-col">
                  {sheets.length > 1 ? (
                    <div className="scroll-soft flex shrink-0 gap-1 overflow-x-auto border-b border-[var(--border)] px-4 py-2">
                      {sheets.map((sheet, idx) => (
                        <button
                          key={sheet.name}
                          type="button"
                          onClick={() => setActiveSheet(idx)}
                          className={`shrink-0 cursor-pointer rounded-[7px] px-2.5 py-1 font-mono text-[11px] font-medium transition-colors ${
                            idx === activeSheet
                              ? 'bg-[var(--hark-card-fg)] text-[var(--hark-card-bg)]'
                              : 'text-[var(--text-muted)] hover:bg-[color-mix(in_srgb,var(--text)_7%,transparent)] hover:text-[var(--text)]'
                          }`}
                        >
                          {sheet.name}
                        </button>
                      ))}
                    </div>
                  ) : null}

                  <div className="scroll-soft min-h-0 flex-1 overflow-auto">
                    {currentSheet && currentSheet.rows.length > 0 ? (
                      <table className="w-full border-collapse text-left text-[12.5px]">
                        <thead className="sticky top-0 z-10 bg-[var(--hark-card-bg)]">
                          <tr>
                            {(currentSheet.rows[0] ?? []).map((cell, cIdx) => (
                              <th
                                key={cIdx}
                                className="border-b border-[var(--border)] px-4 py-2 font-mono text-[11px] font-semibold uppercase tracking-[0.04em] whitespace-nowrap text-[var(--text-muted)]"
                              >
                                {cell || `Col ${cIdx + 1}`}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {currentSheet.rows.slice(1, 401).map((row, rIdx) => (
                            <tr
                              key={rIdx}
                              className="border-b border-[var(--border)] last:border-0 hover:bg-[color-mix(in_srgb,var(--text)_4%,transparent)]"
                            >
                              {row.map((cell, cIdx) => (
                                <td
                                  key={cIdx}
                                  className="px-4 py-2 font-mono tabular-nums whitespace-nowrap text-[var(--text)]"
                                >
                                  {cell}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : (
                      <p className="px-4 py-6 text-[13px] text-[var(--text-muted)]">
                        {loadingBlob ? 'Loading table...' : 'No rows to display.'}
                      </p>
                    )}
                  </div>
                </div>
              ) : (
                <div className="scroll-soft h-full overflow-y-auto">
                  <article className="mx-auto w-full max-w-[72ch] px-6 py-9 sm:px-10">
                    {item.format === 'markdown' ? (
                      <MarkdownContent content={documentText} />
                    ) : item.format === 'docx' ? (
                      documentParagraphs.length > 0 ? (
                        <div className="space-y-4 text-[14.5px] leading-[1.75] text-[var(--text)]">
                          {documentParagraphs.map((paragraph, idx) => (
                            <p key={idx} className="whitespace-pre-wrap">
                              {paragraph}
                            </p>
                          ))}
                        </div>
                      ) : (
                        <p className="text-[13px] text-[var(--text-muted)]">
                          {loadingBlob ? 'Loading document...' : 'Document preview unavailable.'}
                        </p>
                      )
                    ) : (
                      <pre className="whitespace-pre-wrap font-mono text-[13px] leading-[1.75] text-[var(--text)]">
                        {documentText || 'No text preview available.'}
                      </pre>
                    )}
                  </article>
                </div>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

export default ArtifactCard;
