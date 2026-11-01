import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowUpRight, Check, Circle, Clock, Loader2, Sparkles } from 'lucide-react';
import { motion } from 'framer-motion';
import type { ComponentType } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api';
import { userMessageOf } from '@/lib/errors';
import { relativeTime } from '@/lib/format';
import { releaseIdempotencyKey } from '@/lib/idempotency';
import { CARDS_KEY, useDismissCard } from '@/queries/cards';
import type { Card, CardAction } from '@/types';

type IconType = ComponentType<{ size?: number; strokeWidth?: number; className?: string }>;

interface Presentation {
  Icon: IconType;
  label: string;
  accent: boolean;
}

/**
 * The three eyebrows are derived from real fields, not invented archetypes:
 * an approval is waiting, a deadline is approaching, or it is simply on the record.
 */
function presentationFor(card: Card): Presentation {
  if (card.status === 'awaiting_approval') {
    return { Icon: Sparkles, label: 'Action ready', accent: true };
  }
  if (card.due_at) {
    return { Icon: Clock, label: 'Gentle reminder', accent: false };
  }
  return { Icon: Circle, label: 'For the record', accent: false };
}

interface Props {
  card: Card;
  index: number;
  onError: (message: string) => void;
}

export function TaskCard({ card, index, onError }: Props) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const dismissCard = useDismissCard();
  const { Icon, label, accent } = presentationFor(card);

  const due = relativeTime(card.due_at);
  const busy = dismissCard.isPending;

  const runAction = useMutation({
    mutationFn: ({ action, scope }: { action: CardAction; scope: string }) =>
      api.createRun(action.payload, card.id, scope),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: CARDS_KEY });
    },
    onError: (error) => onError(userMessageOf(error)),
    onSettled: (_data, _error, variables) => releaseIdempotencyKey(variables.scope),
  });

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

  const isKnown = (action: CardAction) =>
    action.action_type === 'run' ||
    action.action_type === 'dismiss' ||
    action.action_type === 'reply';

  const [primary, ...rest] = card.actions;
  const isBlocking = card.status === 'awaiting_approval';

  return (
    <motion.article
      layout
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.96, transition: { duration: 0.2 } }}
      transition={{ type: 'spring', stiffness: 400, damping: 30, delay: index * 0.05 }}
      className="card rounded-[28px] p-6 md:p-7"
      aria-labelledby={`card-title-${card.id}`}
    >
      <div className="flex items-center justify-between gap-3">
        <span
          className="inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.12em]"
          style={{ color: accent ? 'var(--accent)' : 'var(--text-muted)' }}
        >
          <Icon size={13} strokeWidth={1.5} />
          {label}
        </span>
        {due ? <span className="shrink-0 text-xs text-[var(--text-muted)]">{due}</span> : null}
      </div>

      <h3
        id={`card-title-${card.id}`}
        className="mt-4 text-[21px] font-semibold leading-snug tracking-[-0.025em]"
      >
        {card.title}
      </h3>
      {card.summary ? (
        <p className="mt-2 text-[15px] leading-relaxed text-[var(--text-muted)]">{card.summary}</p>
      ) : null}

      <div className="mt-6 flex flex-wrap items-center gap-2.5">
        {primary ? (
          <button
            type="button"
            onClick={() => dispatch(primary)}
            disabled={busy || !isKnown(primary)}
            className="btn btn-primary"
          >
            {isBlocking ? (
              <Check size={16} strokeWidth={1.75} />
            ) : (
              <ArrowUpRight size={16} strokeWidth={1.5} />
            )}
            {primary.label}
          </button>
        ) : null}

        {rest.slice(0, 2).map((action) => (
          <button
            key={action.id}
            type="button"
            onClick={() => dispatch(action)}
            disabled={busy || !isKnown(action)}
            className="btn btn-ghost"
          >
            {action.label}
          </button>
        ))}

        {runAction.isPending ? (
          <span className="inline-flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
            <Loader2 size={13} strokeWidth={1.5} className="animate-spin" />
            Starting
          </span>
        ) : null}

        <button
          type="button"
          onClick={() =>
            navigate('/chat', {
              state: { seed: `Tell me about: ${card.title}`, cardId: card.id },
            })
          }
          className="ml-auto inline-flex h-10 items-center gap-1.5 rounded-full px-3 text-sm font-medium text-[var(--accent)] transition-opacity hover:opacity-75 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          Discuss with Vigil
          <ArrowUpRight size={14} strokeWidth={1.5} />
        </button>
      </div>
    </motion.article>
  );
}

export default TaskCard;
