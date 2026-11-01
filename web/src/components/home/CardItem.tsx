import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Calendar,
  Circle,
  Clock,
  Mail,
  TriangleAlert,
  ArrowUpRight,
  ShieldCheck,
} from 'lucide-react';
import type { ComponentType } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api';
import { userMessageOf } from '@/lib/errors';
import { absoluteTime, relativeTime } from '@/lib/format';
import { releaseIdempotencyKey } from '@/lib/idempotency';
import { CARDS_KEY, useDismissCard } from '@/queries/cards';
import type { Card, CardAction } from '@/types';
import { Pill } from '@/components/ui/Pill';
import { StateChip, UrgencyRail } from '@/components/ui/StateChip';

const TYPE_ICON: Record<string, ComponentType<{ size?: number; strokeWidth?: number }>> = {
  email: Mail,
  calendar: Calendar,
  alert: TriangleAlert,
};

const URGENCY_TEXT: Record<Card['urgency'], string> = {
  critical: 'Critical urgency',
  high: 'High urgency',
  medium: 'Medium urgency',
  low: 'Low urgency',
};

interface CardItemProps {
  card: Card;
  onError: (message: string) => void;
}

/**
 * A single card, rendered entirely from the wire schema.
 *
 * Buttons come from `card.actions`, in server order, primary first. Nothing about the action set
 * is hard-coded here: an unknown `action_type` degrades to a disabled ghost button rather than
 * crashing the feed.
 */
export function CardItem({ card, onError }: CardItemProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const dismissCard = useDismissCard();

  const runAction = useMutation({
    mutationFn: ({ action, scope }: { action: CardAction; scope: string }) =>
      api.createRun(action.payload, card.id, scope),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: CARDS_KEY });
    },
    onError: (error) => onError(userMessageOf(error)),
    onSettled: (_data, _error, variables) => releaseIdempotencyKey(variables.scope),
  });

  const Icon = TYPE_ICON[card.type] ?? Circle;
  const due = relativeTime(card.due_at);
  const dueAbsolute = absoluteTime(card.due_at);
  const isBlocking = card.status === 'awaiting_approval';
  const busy = dismissCard.isPending || runAction.isPending;

  const dispatch = (action: CardAction) => {
    const scope = `card-action:${card.id}:${action.id}`;

    switch (action.action_type) {
      case 'run':
        runAction.mutate({ action, scope });
        break;
      case 'dismiss':
        dismissCard.mutate({ cardId: card.id, scope });
        break;
      case 'reply': {
        const seed = typeof action.payload['seed'] === 'string' ? action.payload['seed'] : '';
        navigate('/chat', { state: { seed, cardId: card.id } });
        break;
      }
      default:
        break;
    }
  };

  const isKnownAction = (action: CardAction) =>
    action.action_type === 'run' ||
    action.action_type === 'dismiss' ||
    action.action_type === 'reply';

  const [primary, ...rest] = card.actions;

  return (
    <article
      aria-labelledby={`card-title-${card.id}`}
      aria-describedby={card.summary ? `card-summary-${card.id}` : undefined}
      className={[
        'relative overflow-hidden rounded-[var(--r-card)] border border-hairline bg-card p-4',
        'transition-colors duration-[var(--dur-base)] ease-[var(--ease-spring)]',
      ].join(' ')}
      style={{ boxShadow: 'var(--shadow-card)' }}
    >
      <UrgencyRail urgency={card.urgency} />

      <div className="pl-2">
        <div className="flex items-start gap-3">
          <Icon size={18} strokeWidth={1.5} aria-hidden="true" className="mt-0.5 shrink-0 text-ink-3" />

          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-3">
              <h2
                id={`card-title-${card.id}`}
                className="text-[17px] font-semibold leading-snug tracking-[-0.01em] text-wrap-balance"
              >
                <span className="sr-only">{URGENCY_TEXT[card.urgency]}. </span>
                {card.title}
              </h2>
              <StateChip status={card.status} />
            </div>

            {card.summary ? (
              <p
                id={`card-summary-${card.id}`}
                className="mt-1.5 line-clamp-2 text-[14px] leading-relaxed text-ink-3"
              >
                {card.summary}
              </p>
            ) : null}

            {due ? (
              <p className="mt-2.5 inline-flex items-center gap-1.5 text-[12px] text-ink-3" title={dueAbsolute}>
                <Clock size={14} strokeWidth={1.5} aria-hidden="true" />
                <span className="tabular">{due}</span>
              </p>
            ) : null}
          </div>
        </div>

        <div className="mt-3.5 flex flex-wrap items-center gap-2 pl-8">
          {primary ? (
            <Pill
              variant="primary"
              disabled={busy || !isKnownAction(primary)}
              onClick={() => dispatch(primary)}
              icon={isBlocking ? <ShieldCheck size={16} strokeWidth={1.75} aria-hidden="true" /> : undefined}
            >
              {primary.label}
            </Pill>
          ) : null}

          {rest.slice(0, 2).map((action) => (
            <Pill
              key={action.id}
              variant="ghost"
              disabled={busy || !isKnownAction(action)}
              onClick={() => dispatch(action)}
            >
              {action.label}
            </Pill>
          ))}

          <button
            type="button"
            onClick={() =>
              navigate('/chat', { state: { seed: `Tell me about: ${card.title}`, cardId: card.id } })
            }
            className="ml-auto inline-flex items-center gap-1 rounded-control px-1 text-[12px] text-ink-3 transition-colors duration-[var(--dur-fast)] hover:text-accent"
          >
            Discuss in Chat
            <ArrowUpRight size={14} strokeWidth={1.5} aria-hidden="true" />
          </button>
        </div>
      </div>
    </article>
  );
}
