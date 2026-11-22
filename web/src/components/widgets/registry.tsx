import type { ReactNode } from 'react';
import { z } from 'zod';
import type { Widget } from '@/types';
import { A2UISurface, resolveA2UIIcon, type A2UISurfacePayload } from './A2UISurface';
import { FallbackCard } from './FallbackCard';

export const A2UI_PANEL_CATALOG_ID = 'urn:vigil:a2ui:catalog:panels:v1' as const;

const a2uiComponentNodeSchema = z
  .object({
    id: z.string().min(1).max(64),
    component: z.enum([
      'Column',
      'Row',
      'Divider',
      'Metric',
      'Sparkline',
      'ProgressBar',
      'Badge',
      'KeyValueList',
      'DataTable',
      'Text',
      'CheckList',
      'Button',
      'TextField',
      'ChoiceGroup',
    ]),
    props: z.record(z.unknown()).optional().default({}),
    children: z.array(z.string()).max(8).optional().default([]),
  })
  .strict();

export const a2uiSurfacePayloadSchema = z
  .object({
    version: z.literal('v0.9').optional().default('v0.9'),
    catalog_id: z
      .literal(A2UI_PANEL_CATALOG_ID)
      .optional()
      .default(A2UI_PANEL_CATALOG_ID),
    size: z.enum(['sm', 'md', 'lg']).optional().default('md'),
    icon: z.string().nullable().optional(),
    root: z.string().min(1).max(64),
    components: z.array(a2uiComponentNodeSchema).min(1).max(16),
    data_model: z.record(z.unknown()).optional().default({}),
  })
  .strict()
  .refine(
    (payload) => payload.components.some((node) => node.id === payload.root),
    { message: 'A2UI root component must exist in components' },
  );

export const widgetSchema = z.object({
  id: z.string(),
  title: z.string(),
  widget_type: z.literal('a2ui_surface'),
  data_payload: a2uiSurfacePayloadSchema,
  version: z.number(),
  updated_at: z.string(),
});

export type ParsedWidget = z.infer<typeof widgetSchema>;

export interface WidgetViewProps {
  widget: Widget;
  trailing?: ReactNode;
  onAskVigil?: (prompt?: string) => void;
  onRefreshPanel?: () => void;
}

/**
 * One A2UI dashboard panel. Parses with safeParse and renders FallbackCard on
 * any failure, so a malformed row or unknown type never blanks the rail.
 */
export function WidgetView({ widget, trailing, onAskVigil, onRefreshPanel }: WidgetViewProps) {
  const parsed = widgetSchema.safeParse({
    id: widget.id,
    title: widget.title,
    widget_type: widget.widget_type,
    data_payload: widget.data_payload,
    version: widget.version,
    updated_at: widget.updated_at,
  });

  if (!parsed.success) {
    return <FallbackCard title={widget.title} updatedAt={widget.updated_at} />;
  }

  const surface = parsed.data.data_payload as A2UISurfacePayload;
  const Icon = resolveA2UIIcon(surface.icon);

  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2 text-[13px] font-semibold text-[var(--text)]">
          <span className="shrink-0 text-[var(--text-muted)]">
            <Icon size={15} strokeWidth={1.6} />
          </span>
          <span className="truncate">{parsed.data.title}</span>
        </div>
        {trailing ? <div className="flex shrink-0 items-center gap-0.5">{trailing}</div> : null}
      </div>
      <div className="mt-3">
        <A2UISurface
          surface={surface}
          onAskVigil={onAskVigil}
          onRefreshPanel={onRefreshPanel}
        />
      </div>
    </div>
  );
}

export default WidgetView;
