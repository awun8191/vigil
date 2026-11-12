import { z } from 'zod';
import type { ComponentType } from 'react';
import type { Widget } from '@/types';
import { MetricCard } from './MetricCard';
import { DataTable } from './DataTable';
import { TaskList } from './TaskList';
import { StatusFeed } from './StatusFeed';
import { FallbackCard } from './FallbackCard';

/* Payload shapes mirror backend/src/modules/widgets/data.py caps. */

const metricPayloadSchema = z
  .object({
    value: z.string().min(1).max(64),
    delta: z.string().max(64).optional(),
    caption: z.string().max(200).optional(),
  })
  .strict();

const tablePayloadSchema = z
  .object({
    columns: z.array(z.string()).min(1).max(8),
    rows: z.array(z.array(z.string())).max(50),
  })
  .strict();

const taskItemSchema = z
  .object({
    id: z.string().min(1).max(64),
    label: z.string().min(1).max(200),
    done: z.boolean().optional().default(false),
  })
  .strict();

const taskListPayloadSchema = z
  .object({
    items: z.array(taskItemSchema).max(50),
  })
  .strict();

const feedEntrySchema = z
  .object({
    ts: z.string().min(1).max(64),
    text: z.string().min(1).max(280),
  })
  .strict();

const feedPayloadSchema = z
  .object({
    entries: z.array(feedEntrySchema).max(30),
  })
  .strict();

const baseWidgetSchema = z.object({
  id: z.string(),
  title: z.string(),
  version: z.number(),
  updated_at: z.string(),
});

export const widgetSchema = z.discriminatedUnion('widget_type', [
  baseWidgetSchema.extend({ widget_type: z.literal('metric_card'), data_payload: metricPayloadSchema }),
  baseWidgetSchema.extend({ widget_type: z.literal('data_table'), data_payload: tablePayloadSchema }),
  baseWidgetSchema.extend({ widget_type: z.literal('task_list'), data_payload: taskListPayloadSchema }),
  baseWidgetSchema.extend({ widget_type: z.literal('status_feed'), data_payload: feedPayloadSchema }),
]);

export type ParsedWidget = z.infer<typeof widgetSchema>;

export const WIDGET_MAP = {
  metric_card: MetricCard,
  data_table: DataTable,
  task_list: TaskList,
  status_feed: StatusFeed,
} satisfies Record<string, ComponentType<never>>;

function renderPayload(widget: ParsedWidget) {
  switch (widget.widget_type) {
    case 'metric_card':
      return (
        <MetricCard value={widget.data_payload.value} delta={widget.data_payload.delta} caption={widget.data_payload.caption} />
      );
    case 'data_table':
      return <DataTable columns={widget.data_payload.columns} rows={widget.data_payload.rows} />;
    case 'task_list':
      return <TaskList items={widget.data_payload.items} />;
    case 'status_feed':
      return <StatusFeed entries={widget.data_payload.entries} />;
  }
}

/**
 * One dashboard panel. Parses with safeParse and renders the fallback card on
 * any failure, so a bad row or an unknown type never blanks the rail.
 */
export function WidgetView({ widget }: { widget: Widget }) {
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
  return (
    <div>
      <div className="truncate pr-6 text-sm font-medium">{parsed.data.title}</div>
      <div className="mt-2">{renderPayload(parsed.data)}</div>
    </div>
  );
}

export default WidgetView;
