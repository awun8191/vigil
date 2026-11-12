import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { releaseIdempotencyKey } from '@/lib/idempotency';
import type { StreamState, Widget, WidgetUpdatedEvent } from '@/types';

export const WIDGETS_KEY = ['widgets'] as const;

/** Fetch the dashboard panel list, newest first. The backend orders by updated_at desc. */
export function useWidgets() {
  return useQuery<Widget[]>({
    queryKey: WIDGETS_KEY,
    queryFn: () => api.listWidgets(),
    staleTime: 15_000,
    refetchOnWindowFocus: true,
  });
}

/**
 * Merge one widget_updated frame into the cached list.
 *
 * Replaces the row only when the incoming version is newer. Stale frames and
 * unknown ids leave the list untouched. Exported for focused tests.
 */
export function applyWidgetUpdate(widgets: Widget[], event: WidgetUpdatedEvent): Widget[] {
  let touched = false;
  const next = widgets.map((widget) => {
    if (widget.id !== event.id) return widget;
    if (event.version <= widget.version) return widget;
    touched = true;
    return {
      ...widget,
      data_payload: event.data_payload,
      version: event.version,
      updated_at: event.updated_at,
    };
  });
  return touched ? next : widgets;
}

function parseWidgetEvent(data: string): WidgetUpdatedEvent | null {
  let raw: unknown;
  try {
    raw = JSON.parse(data) as unknown;
  } catch {
    return null;
  }
  if (typeof raw !== 'object' || raw === null) return null;
  const record = raw as Record<string, unknown>;
  if (typeof record['id'] !== 'string') return null;
  if (typeof record['version'] !== 'number') return null;
  if (typeof record['data_payload'] !== 'object' || record['data_payload'] === null) return null;
  if (typeof record['updated_at'] !== 'string') return null;
  return {
    id: record['id'],
    version: record['version'],
    data_payload: record['data_payload'] as Record<string, unknown>,
    updated_at: record['updated_at'],
  };
}

/**
 * One dashboard-level subscription to GET /widgets/stream.
 *
 * The transport replays with Last-Event-ID on reconnect, so a frame that lands
 * between the list fetch and the stream open still arrives and merges by
 * version. Not one stream per widget.
 */
export function useWidgetsStream() {
  const queryClient = useQueryClient();
  const [connection, setConnection] = useState<StreamState>('idle');

  useEffect(() => {
    const close = api.openWidgetsStream({
      handlers: {
        onEvent(event) {
          if (event.type !== 'widget_updated') return;
          const parsed = parseWidgetEvent(event.data);
          if (!parsed) return;
          queryClient.setQueryData<Widget[]>(WIDGETS_KEY, (current) =>
            current ? applyWidgetUpdate(current, parsed) : current,
          );
        },
        onStateChange(state) {
          setConnection(state);
        },
      },
    });
    return () => {
      close();
    };
  }, [queryClient]);

  return { connection };
}

/** Delete one dashboard panel with an optimistic removal and a rollback on error. */
export function useDeleteWidget() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ widgetId, scope }: { widgetId: string; scope: string }) =>
      api.deleteWidget(widgetId, scope),

    onMutate: async ({ widgetId }) => {
      await queryClient.cancelQueries({ queryKey: WIDGETS_KEY });
      const previous = queryClient.getQueryData<Widget[]>(WIDGETS_KEY);

      queryClient.setQueryData<Widget[]>(WIDGETS_KEY, (current) =>
        current ? current.filter((widget) => widget.id !== widgetId) : current,
      );

      return { previous };
    },

    onError: (_error, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(WIDGETS_KEY, context.previous);
    },

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: WIDGETS_KEY });
    },
  });
}
