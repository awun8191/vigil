import {
  Activity,
  Calendar,
  Check,
  CheckSquare,
  Clock,
  CreditCard,
  Flame,
  GitBranch,
  Globe,
  HeartPulse,
  Inbox,
  Layers,
  LayoutDashboard,
  Mail,
  Music,
  Package,
  Sparkles,
  TrainFront,
  TrendingUp,
  Zap,
} from 'lucide-react';
import type { ComponentType, ReactNode } from 'react';

export type A2UITone = 'neutral' | 'accent' | 'positive' | 'warning' | 'danger';
export type A2UIEmphasis = 'hero' | 'primary' | 'secondary' | 'muted' | 'mono';
export type A2UIGap = 'tight' | 'normal' | 'relaxed';
export type A2UIAlign = 'start' | 'center' | 'end' | 'between';
export type A2UISize = 'sm' | 'md' | 'lg';

export interface A2UIPointer {
  path: string;
}

export type A2UIValue<T> = T | A2UIPointer;

export interface A2UIComponentNode {
  id: string;
  component:
    | 'Column'
    | 'Row'
    | 'Divider'
    | 'Metric'
    | 'Sparkline'
    | 'ProgressBar'
    | 'Badge'
    | 'KeyValueList'
    | 'DataTable'
    | 'Text'
    | 'CheckList'
    | 'Button'
    | 'TextField'
    | 'ChoiceGroup';
  props?: Record<string, unknown>;
  children?: string[];
}

export interface A2UISurfacePayload {
  version: 'v0.9';
  catalog_id: 'urn:vigil:a2ui:catalog:panels:v1';
  size?: A2UISize;
  icon?: string | null;
  root: string;
  components: A2UIComponentNode[];
  data_model: Record<string, unknown>;
}

type IconType = ComponentType<{ size?: number; strokeWidth?: number; className?: string }>;

const ICON_MAP: Record<string, IconType> = {
  activity: Activity,
  calendar: Calendar,
  'check-square': CheckSquare,
  clock: Clock,
  'credit-card': CreditCard,
  flame: Flame,
  'git-branch': GitBranch,
  globe: Globe,
  'heart-pulse': HeartPulse,
  inbox: Inbox,
  layers: Layers,
  mail: Mail,
  music: Music,
  package: Package,
  sparkles: Sparkles,
  'train-front': TrainFront,
  'trending-up': TrendingUp,
  zap: Zap,
};

export function resolveA2UIIcon(iconName?: string | null): IconType {
  if (!iconName) return LayoutDashboard;
  return ICON_MAP[iconName] ?? LayoutDashboard;
}

export function isA2UIPointer(value: unknown): value is A2UIPointer {
  return (
    typeof value === 'object' &&
    value !== null &&
    'path' in value &&
    typeof (value as { path: unknown }).path === 'string' &&
    Object.keys(value).length === 1
  );
}

/**
 * Evaluate an RFC 6901 JSON Pointer (`/a/b/0`, `~0` -> `~`, `~1` -> `/`) against `dataModel`.
 */
export function resolveJsonPointer(dataModel: Record<string, unknown>, path: string): unknown {
  if (path === '/') return dataModel;
  if (!path.startsWith('/')) return undefined;
  const segments = path.split('/').slice(1);
  let current: unknown = dataModel;
  for (const raw of segments) {
    const segment = raw.replace(/~1/g, '/').replace(/~0/g, '~');
    if (Array.isArray(current)) {
      const idx = Number.parseInt(segment, 10);
      if (Number.isNaN(idx) || idx < 0 || idx >= current.length) return undefined;
      current = current[idx];
    } else if (typeof current === 'object' && current !== null) {
      const record = current as Record<string, unknown>;
      if (!(segment in record)) return undefined;
      current = record[segment];
    } else {
      return undefined;
    }
  }
  return current;
}

/**
 * Immutably update a nested value at an RFC 6901 JSON Pointer path inside `dataModel`.
 */
export function setJsonPointerValue(
  dataModel: Record<string, unknown>,
  path: string,
  value: unknown,
): Record<string, unknown> {
  if (path === '/' && typeof value === 'object' && value !== null) {
    return { ...(value as Record<string, unknown>) };
  }
  if (!path.startsWith('/')) return dataModel;
  const segments = path
    .split('/')
    .slice(1)
    .map((s) => s.replace(/~1/g, '/').replace(/~0/g, '~'));
  if (segments.length === 0) return dataModel;

  const clone: Record<string, unknown> = structuredClone(dataModel);
  let current: Record<string, unknown> = clone;
  for (let i = 0; i < segments.length - 1; i += 1) {
    const seg = segments[i]!;
    const existing = current[seg];
    if (typeof existing !== 'object' || existing === null || Array.isArray(existing)) {
      current[seg] = {};
    }
    current = current[seg] as Record<string, unknown>;
  }
  current[segments[segments.length - 1]!] = value;
  return clone;
}

export function resolveBinding<T>(
  raw: unknown,
  dataModel: Record<string, unknown>,
  fallback: T,
): T {
  if (raw === undefined || raw === null) return fallback;
  if (isA2UIPointer(raw)) {
    const resolved = resolveJsonPointer(dataModel, raw.path);
    return resolved !== undefined && resolved !== null ? (resolved as T) : fallback;
  }
  return raw as T;
}

function toneTextClass(tone: A2UITone = 'neutral'): string {
  switch (tone) {
    case 'accent':
      return 'text-[var(--text)]';
    case 'positive':
      return 'text-emerald-400';
    case 'warning':
      return 'text-[var(--tag-decision-text,#fcd34d)]';
    case 'danger':
      return 'text-[var(--tag-urgent-text,#fca5a5)]';
    default:
      return 'text-[var(--text)]';
  }
}

function toneBadgeClass(tone: A2UITone = 'neutral'): string {
  switch (tone) {
    case 'accent':
      return 'bg-[var(--panel-inset-strong)] text-[var(--text)]';
    case 'positive':
      return 'bg-emerald-500/12 text-emerald-300';
    case 'warning':
      return 'bg-[var(--tag-decision-bg)] text-[var(--tag-decision-text)]';
    case 'danger':
      return 'bg-[var(--tag-urgent-bg)] text-[var(--tag-urgent-text)]';
    default:
      return 'bg-[var(--panel-inset-strong)] text-[var(--text-secondary)]';
  }
}

function toneStrokeColor(tone: A2UITone = 'accent'): string {
  switch (tone) {
    case 'positive':
      return '#34d399';
    case 'warning':
      return 'var(--tag-decision-text, #fbbf24)';
    case 'danger':
      return 'var(--tag-urgent-text, #f87171)';
    case 'neutral':
      return 'var(--text-muted)';
    default:
      return 'var(--text)';
  }
}

function gapClass(gap: A2UIGap = 'normal', direction: 'col' | 'row'): string {
  if (direction === 'col') {
    if (gap === 'tight') return 'space-y-2';
    if (gap === 'relaxed') return 'space-y-3.5';
    return 'space-y-3';
  }
  if (gap === 'tight') return 'gap-1.5';
  if (gap === 'relaxed') return 'gap-3';
  return 'gap-2';
}

function alignClass(align: A2UIAlign = 'center'): string {
  switch (align) {
    case 'start':
      return 'items-start justify-start';
    case 'end':
      return 'items-center justify-end';
    case 'between':
      return 'items-center justify-between';
    default:
      return 'items-center justify-start';
  }
}

function parsePercentageMetric(value: string): number | null {
  const trimmed = value.trim();
  const match = /^(\d+(?:\.\d+)?)\s*%$/.exec(trimmed);
  if (!match) return null;
  const num = Number.parseFloat(match[1]!);
  if (!Number.isFinite(num)) return null;
  return Math.max(0, Math.min(100, Math.round(num)));
}

function SparklineGraphic({
  points,
  label,
  tone,
}: {
  points: number[];
  label?: string;
  tone: A2UITone;
}) {
  const valid = points.filter((n) => typeof n === 'number' && Number.isFinite(n));
  if (valid.length < 2) {
    return <div className="text-xs text-[var(--text-muted)]">No trend data</div>;
  }
  const width = 220;
  const height = 44;
  const min = Math.min(...valid);
  const max = Math.max(...valid);
  const span = max - min || 1;
  const coords = valid.map((val, idx) => {
    const x = (idx / (valid.length - 1)) * (width - 8) + 4;
    const y = height - 6 - ((val - min) / span) * (height - 12);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const stroke = toneStrokeColor(tone);

  return (
    <div className="rail-inset space-y-1.5 rounded-[14px] p-3">
      {label ? <div className="truncate text-[11px] text-[var(--text-muted)]">{label}</div> : null}
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-11 w-full overflow-visible"
        role="img"
        aria-label={label || 'Sparkline trend'}
      >
        <polyline
          fill="none"
          stroke={stroke}
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          points={coords.join(' ')}
        />
      </svg>
    </div>
  );
}

export interface A2UISurfaceProps {
  surface: A2UISurfacePayload;
  onAskVigil?: (prompt?: string) => void;
  onRefreshPanel?: () => void;
  onUpdateDataModel?: (path: string, value: unknown) => void;
  onEventAction?: (name: string, context?: Record<string, unknown>) => void;
  disabled?: boolean;
}

export function A2UISurface({
  surface,
  onAskVigil,
  onRefreshPanel,
  onUpdateDataModel,
  onEventAction,
  disabled = false,
}: A2UISurfaceProps) {
  const byId = new Map<string, A2UIComponentNode>();
  for (const node of surface.components) {
    byId.set(node.id, node);
  }

  const visited = new Set<string>();

  function renderNode(nodeId: string, depth: number): ReactNode {
    if (depth > 3 || visited.has(nodeId)) return null;
    const node = byId.get(nodeId);
    if (!node) return null;
    visited.add(nodeId);

    const props = node.props ?? {};
    const childrenIds = node.children ?? [];
    const dataModel = surface.data_model ?? {};

    switch (node.component) {
      case 'Column': {
        const gap = (props['gap'] as A2UIGap | undefined) ?? 'normal';
        return (
          <div key={node.id} className={gapClass(gap, 'col')}>
            {childrenIds.map((childId) => renderNode(childId, depth + 1))}
          </div>
        );
      }

      case 'Row': {
        const gap = (props['gap'] as A2UIGap | undefined) ?? 'normal';
        const align = (props['align'] as A2UIAlign | undefined) ?? 'center';
        const wrap = Boolean(props['wrap']);
        return (
          <div
            key={node.id}
            className={`flex ${gapClass(gap, 'row')} ${alignClass(align)} ${wrap ? 'flex-wrap' : ''}`}
          >
            {childrenIds.map((childId) => renderNode(childId, depth + 1))}
          </div>
        );
      }

      case 'Divider':
        return <hr key={node.id} className="border-t border-[var(--border)]" />;

      case 'Metric': {
        const label = String(resolveBinding(props['label'], dataModel, 'Metric'));
        const value = String(resolveBinding(props['value'], dataModel, '—'));
        const delta = resolveBinding<string | null>(props['delta'], dataModel, null);
        const caption = resolveBinding<string | null>(props['caption'], dataModel, null);
        const tone = (props['tone'] as A2UITone | undefined) ?? 'neutral';
        const pct = parsePercentageMetric(value);

        return (
          <div
            key={node.id}
            className="rail-inset min-w-0 flex-1 rounded-[14px] px-3 py-2.5"
          >
            <div className="truncate text-[11px] font-medium text-[var(--text-muted)]">
              {label}
            </div>
            <div className="mt-1 flex flex-wrap items-baseline justify-between gap-2">
              <span className="truncate text-[24px] font-semibold leading-none tracking-[-0.03em] tabular text-[var(--text)]">
                {value}
              </span>
              {delta ? (
                <span
                  className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-medium tabular ${toneBadgeClass(tone)}`}
                >
                  {delta}
                </span>
              ) : null}
            </div>
            {pct !== null ? (
              <div className="mt-2.5 h-1.5 w-full overflow-hidden rounded-full bg-[color-mix(in_srgb,var(--text)_10%,transparent)]">
                <div
                  className="h-full rounded-full transition-all duration-300"
                  style={{ width: `${pct}%`, background: toneStrokeColor(tone) }}
                />
              </div>
            ) : null}
            {caption ? (
              <div className="mt-1.5 text-[11.5px] leading-snug text-[var(--text-secondary)]">
                {caption}
              </div>
            ) : null}
          </div>
        );
      }

      case 'Sparkline': {
        const points = resolveBinding<number[]>(props['points'], dataModel, []);
        const label = resolveBinding<string | undefined>(props['label'], dataModel, undefined);
        const tone = (props['tone'] as A2UITone | undefined) ?? 'accent';
        return (
          <SparklineGraphic
            key={node.id}
            points={Array.isArray(points) ? points : []}
            label={label}
            tone={tone}
          />
        );
      }

      case 'ProgressBar': {
        const label = resolveBinding<string | undefined>(props['label'], dataModel, undefined);
        const rawValue = Number(resolveBinding(props['value'], dataModel, 0));
        const max = Number(props['max'] ?? 100) || 100;
        const pct = Math.max(0, Math.min(100, Math.round((rawValue / max) * 100)));
        const showValue = props['show_value'] !== false;
        const tone = (props['tone'] as A2UITone | undefined) ?? 'accent';
        return (
          <div key={node.id} className="rail-inset space-y-1.5 rounded-[14px] px-3 py-2.5">
            {label || showValue ? (
              <div className="flex items-center justify-between gap-2 text-[11.5px]">
                <span className="truncate text-[var(--text-muted)]">{label ?? ''}</span>
                {showValue ? (
                  <span className="shrink-0 font-medium tabular text-[var(--text)]">
                    {' '}
                    {pct}%
                  </span>
                ) : null}
              </div>
            ) : null}
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-[color-mix(in_srgb,var(--text)_10%,transparent)]">
              <div
                className="h-full rounded-full transition-all duration-300"
                style={{ width: `${pct}%`, background: toneStrokeColor(tone) }}
              />
            </div>
          </div>
        );
      }

      case 'Badge': {
        const text = String(resolveBinding(props['text'], dataModel, ''));
        const tone = (props['tone'] as A2UITone | undefined) ?? 'neutral';
        return (
          <span
            key={node.id}
            className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-medium ${toneBadgeClass(tone)}`}
          >
            {text}
          </span>
        );
      }

      case 'KeyValueList': {
        const items = resolveBinding<
          Array<{ label: string; value: string; meta?: string; tone?: A2UITone }>
        >(props['items'], dataModel, []);
        const maxItems = Number(props['max_items'] ?? 5) || 5;
        const list = Array.isArray(items) ? items.slice(0, maxItems) : [];
        if (list.length === 0) {
          return (
            <div
              key={node.id}
              className="rail-inset rounded-[14px] px-3 py-2.5 text-[12px] text-[var(--text-muted)]"
            >
              No items yet.
            </div>
          );
        }
        return (
          <dl
            key={node.id}
            className="rail-inset divide-y divide-[var(--border)] rounded-[14px] px-3 py-1.5"
          >
            {list.map((item, idx) => {
              const valText = String(item.value ?? '');
              const isCompactValue = valText.length <= 16 && !valText.includes('\n');
              const tone = item.tone ?? 'neutral';

              if (isCompactValue) {
                return (
                  <div
                    key={`${node.id}-${idx}`}
                    className="flex items-baseline justify-between gap-3 py-1.5"
                  >
                    <dt className="min-w-0 truncate text-[11.5px] text-[var(--text-muted)]">
                      {item.label}
                      {item.meta ? (
                        <span className="ml-1.5 text-[10.5px] text-[var(--text-muted)]">
                          · {item.meta}
                        </span>
                      ) : null}
                    </dt>
                    <dd
                      className={`shrink-0 text-[12px] font-medium tabular ${toneTextClass(tone)}`}
                    >
                      {valText}
                    </dd>
                  </div>
                );
              }

              return (
                <div key={`${node.id}-${idx}`} className="py-2">
                  <dt className="flex items-center justify-between gap-2">
                    <span className="truncate text-[10.5px] font-semibold uppercase tracking-[0.06em] text-[var(--text-muted)]">
                      {item.label}
                    </span>
                    {item.meta ? (
                      <span className="shrink-0 text-[10.5px] text-[var(--text-muted)]">
                        {item.meta}
                      </span>
                    ) : null}
                  </dt>
                  <dd
                    className={`mt-0.5 text-[12px] font-medium leading-snug ${toneTextClass(tone)}`}
                  >
                    {valText}
                  </dd>
                </div>
              );
            })}
          </dl>
        );
      }

      case 'DataTable': {
        const columns = Array.isArray(props['columns']) ? (props['columns'] as string[]) : [];
        const rows = resolveBinding<string[][]>(props['rows'], dataModel, []);
        const safeRows = Array.isArray(rows) ? rows : [];
        return (
          <div key={node.id} className="rail-inset overflow-x-auto rounded-[14px] p-2">
            <table className="w-full border-collapse text-[12px]">
              <thead>
                <tr>
                  {columns.map((col) => (
                    <th
                      key={col}
                      scope="col"
                      className="border-b border-[var(--border)] px-2 py-1 text-left text-[11px] font-medium text-[var(--text-muted)]"
                    >
                      {col}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {safeRows.map((row, rIdx) => (
                  <tr key={rIdx}>
                    {columns.map((col, cIdx) => (
                      <td
                        key={`${col}-${cIdx}`}
                        className="border-b border-[var(--border)] px-2 py-1.5 align-top text-[var(--text)] last:border-b-0"
                      >
                        {Array.isArray(row) ? (row[cIdx] ?? '') : ''}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {safeRows.length === 0 ? (
              <p className="px-2 py-1.5 text-[11.5px] text-[var(--text-muted)]">No rows yet.</p>
            ) : null}
          </div>
        );
      }

      case 'Text': {
        const text = String(resolveBinding(props['text'], dataModel, ''));
        const emphasis = (props['emphasis'] as A2UIEmphasis | undefined) ?? 'primary';
        const styleMap: Record<A2UIEmphasis, string> = {
          hero: 'text-[13.5px] font-semibold tracking-[-0.01em] text-[var(--text)]',
          primary: 'text-[12px] leading-relaxed text-[var(--text-secondary)]',
          secondary: 'text-[11.5px] leading-relaxed text-[var(--text-muted)]',
          muted: 'text-[11px] leading-snug text-[var(--text-muted)]',
          mono: 'font-mono text-[11px] text-[var(--text-muted)]',
        };
        return (
          <p key={node.id} className={styleMap[emphasis] ?? styleMap.primary}>
            {text}
          </p>
        );
      }

      case 'CheckList': {
        const items = resolveBinding<Array<{ id: string; label: string; done?: boolean }>>(
          props['items'],
          dataModel,
          [],
        );
        const list = Array.isArray(items) ? items : [];
        if (list.length === 0) {
          return (
            <div
              key={node.id}
              className="rail-inset rounded-[14px] px-3 py-2.5 text-[12px] text-[var(--text-muted)]"
            >
              No tasks yet.
            </div>
          );
        }
        return (
          <ul
            key={node.id}
            className="rail-inset divide-y divide-[var(--border)] rounded-[14px] px-3 py-1"
          >
            {list.map((item) => {
              const done = Boolean(item.done);
              return (
                <li key={item.id} className="flex items-start gap-2.5 py-2">
                  <span
                    aria-hidden="true"
                    className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-[5px] border ${
                      done
                        ? 'border-[var(--text)] bg-[var(--text)] text-[var(--surface-solid)]'
                        : 'border-[var(--border-strong)] bg-transparent'
                    }`}
                  >
                    {done ? <Check size={10} strokeWidth={2.6} /> : null}
                  </span>
                  <span
                    className={`text-[12px] leading-snug ${
                      done
                        ? 'text-[var(--text-muted)] line-through'
                        : 'font-medium text-[var(--text)]'
                    }`}
                  >
                    {item.label}
                  </span>
                </li>
              );
            })}
          </ul>
        );
      }

      case 'TextField': {
        const label = resolveBinding<string | undefined>(props['label'], dataModel, undefined);
        const placeholder =
          typeof props['placeholder'] === 'string'
            ? props['placeholder']
            : 'Type your answer...';
        const pointerPath = isA2UIPointer(props['value']) ? props['value'].path : null;
        const currentValue = String(resolveBinding(props['value'], dataModel, ''));

        return (
          <div key={node.id} className="space-y-1.5">
            {label ? (
              <label className="block text-[12.5px] font-medium text-[var(--text)]">
                {label}
              </label>
            ) : null}
            <input
              type="text"
              value={currentValue}
              disabled={disabled}
              placeholder={placeholder}
              onChange={(e) => {
                if (pointerPath) {
                  onUpdateDataModel?.(pointerPath, e.target.value);
                }
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  onEventAction?.('submit_answers');
                }
              }}
              className="w-full rounded-[14px] border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_2.5%,transparent)] px-3.5 py-2.5 text-[12.5px] text-[var(--text)] placeholder:text-[var(--text-muted)] focus:border-[var(--text)] focus:outline-none disabled:opacity-60"
            />
          </div>
        );
      }

      case 'ChoiceGroup': {
        const label = resolveBinding<string | undefined>(props['label'], dataModel, undefined);
        const multiSelect = Boolean(props['multi_select']);
        const rawOptions = Array.isArray(props['options']) ? props['options'] : [];
        const options = rawOptions
          .map((item) => {
            if (typeof item === 'string') return { label: item, description: null };
            if (typeof item === 'object' && item !== null) {
              const rec = item as Record<string, unknown>;
              const optLabel = typeof rec.label === 'string' ? rec.label : '';
              if (!optLabel) return null;
              return {
                label: optLabel,
                description: typeof rec.description === 'string' ? rec.description : null,
              };
            }
            return null;
          })
          .filter((x): x is { label: string; description: string | null } => x !== null);

        const pointerPath = isA2UIPointer(props['value']) ? props['value'].path : null;
        const resolvedVal = resolveBinding<string | string[]>(
          props['value'],
          dataModel,
          multiSelect ? [] : '',
        );
        const selectedList: string[] = Array.isArray(resolvedVal)
          ? resolvedVal.map(String)
          : resolvedVal
            ? [String(resolvedVal)]
            : [];

        const toggleChoice = (optLabel: string) => {
          if (disabled || !pointerPath) return;
          if (multiSelect) {
            const next = selectedList.includes(optLabel)
              ? selectedList.filter((item) => item !== optLabel)
              : [...selectedList, optLabel];
            onUpdateDataModel?.(pointerPath, next);
          } else {
            onUpdateDataModel?.(pointerPath, optLabel);
          }
        };

        return (
          <div key={node.id} className="space-y-2">
            {label ? (
              <div className="text-[12.5px] font-medium text-[var(--text)]">{label}</div>
            ) : null}
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {options.map((opt) => {
                const isSelected = selectedList.includes(opt.label);
                const isRecommended = opt.label.toLowerCase().includes('recommended');
                return (
                  <button
                    key={opt.label}
                    type="button"
                    disabled={disabled}
                    onClick={() => toggleChoice(opt.label)}
                    className={`flex cursor-pointer items-start gap-2.5 rounded-[14px] border px-3.5 py-2.5 text-left transition-all ${
                      isSelected
                        ? 'border-[var(--text)] bg-[color-mix(in_srgb,var(--text)_8%,transparent)] shadow-xs'
                        : 'border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_2.5%,transparent)] hover:border-[var(--border-strong)] hover:bg-[color-mix(in_srgb,var(--text)_5%,transparent)]'
                    } disabled:cursor-default disabled:opacity-60`}
                  >
                    <span
                      className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center border ${
                        multiSelect ? 'rounded-[4px]' : 'rounded-full'
                      } ${
                        isSelected
                          ? 'border-[var(--text)] bg-[var(--text)] text-[var(--surface-solid)]'
                          : 'border-[var(--border-strong)]'
                      }`}
                    >
                      {isSelected ? <Check size={11} strokeWidth={2.5} /> : null}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-1.5 text-[12.5px] font-medium text-[var(--text)]">
                        <span>{opt.label.replace(/\s*\(Recommended\)/i, '')}</span>
                        {isRecommended ? (
                          <span className="rounded-full bg-[var(--panel-inset)] px-1.5 py-0.5 text-[10px] font-semibold text-[var(--text-muted)]">
                            Recommended
                          </span>
                        ) : null}
                      </span>
                      {opt.description ? (
                        <span className="mt-0.5 block text-[11.5px] leading-snug text-[var(--text-muted)]">
                          {opt.description}
                        </span>
                      ) : null}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        );
      }

      case 'Button': {
        const label = String(resolveBinding(props['label'], dataModel, 'Action'));
        const variant = (props['variant'] as 'primary' | 'secondary' | 'ghost') ?? 'secondary';
        const action = (props['action'] ?? {}) as {
          functionCall?: { call?: string; prompt?: string };
          event?: { name?: string; context?: Record<string, unknown> };
        };
        const variantClasses =
          variant === 'primary'
            ? 'h-9 w-full bg-[var(--btn-primary-bg)] text-[var(--btn-primary-ink)] hover:scale-[1.01] active:scale-[0.99]'
            : variant === 'ghost'
              ? 'text-[var(--text-muted)] hover:text-[var(--text)]'
              : 'border border-[var(--border)] bg-[var(--panel-inset)] text-[var(--text)] hover:bg-[var(--panel-inset-strong)]';

        const handleClick = () => {
          if (disabled) return;
          if (action.event?.name) {
            onEventAction?.(action.event.name, action.event.context);
            return;
          }
          if (action.functionCall?.call === 'refresh_panel') {
            onRefreshPanel?.();
            return;
          }
          onAskVigil?.(action.functionCall?.prompt);
        };

        return (
          <button
            key={node.id}
            type="button"
            disabled={disabled}
            onClick={handleClick}
            className={`inline-flex cursor-pointer items-center justify-center rounded-full px-3.5 py-1.5 text-[12px] font-semibold transition-all disabled:cursor-not-allowed disabled:opacity-45 ${variantClasses}`}
          >
            {label}
          </button>
        );
      }
    }
  }

  return <div>{renderNode(surface.root, 1)}</div>;
}

export default A2UISurface;
