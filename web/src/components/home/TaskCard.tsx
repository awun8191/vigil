import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowUpRight, Check } from 'lucide-react';
import { motion, useReducedMotion } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api';
import { userMessageOf } from '@/lib/errors';
import { relativeTime } from '@/lib/format';
import { releaseIdempotencyKey } from '@/lib/idempotency';
import { CARDS_KEY, useDismissCard } from '@/queries/cards';
import NotificationCard from '@/components/ui/NotificationCard';
import { SourceAvatar, sourceFor } from '@/components/ui/SourceAvatar';
import type { Card, CardAction } from '@/types';

/** Matches MessageItem so briefings and chat arrivals share the same physical feel. */
const enterSpring = {
  type: 'spring' as const,
  stiffness: 480,
  damping: 20,
  mass: 0.9,
};

interface Props {
  card: Card;
  index: number;
  onError: (message: string) => void;
}

/**
 * A briefing item rendered as the standardized notification card: a circular source avatar, the
 * description, and a dropdown that opens its detail and actions. The shell itself is reusable on
 * any page; only this file knows card actions.
 */
export function TaskCard({ card, index, onError }: Props) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const dismissCard = useDismissCard();
  const reduced = useReducedMotion();

  const source = sourceFor(card.type);
  const due = relativeTime(card.due_at);

  const runAction = useMutation({
    mutationFn: ({ action, scope }: { action: CardAction; scope: string }) =>
      api.createRun(action.payload, card.id, scope),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: CARDS_KEY });
    },
    onError: (error) => onError(userMessageOf(error)),
    onSettled: (_data, _error, variables) => releaseIdempotencyKey(variables.scope),
  });

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
        navigate('/agent/chat', { state: { seed, cardId: card.id } });
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

  const statusLabel = isBlocking ? 'Waiting on you' : card.due_at ? 'Gentle reminder' : 'For the record';

  return (
    <motion.div
      layout
      initial={reduced ? { opacity: 0 } : { opacity: 0, y: 18, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, scale: 0.96, transition: { duration: 0.2 } }}
      transition={reduced ? { duration: 0.2 } : { ...enterSpring, delay: index * 0.07 }}
      style={{ transformOrigin: 'top center' }}
    >
      <NotificationCard
        bodyId={`card-body-${card.id}`}
        avatar={<SourceAvatar size={36} source={source} />}
        meta={
          <>
            <span>{source.name}</span>
            <span aria-hidden="true">·</span>
            <span className="font-medium tracking-normal">{statusLabel}</span>
            {due ? (
              <>
                <span aria-hidden="true">·</span>
                <span className="font-medium tracking-normal">{due}</span>
              </>
            ) : null}
          </>
        }
        title={card.title}
      >
        {card.summary ? (
          <p className="text-[14px] leading-relaxed text-[var(--text-muted)]">{card.summary}</p>
        ) : null}

        <div className="mt-4 flex flex-wrap items-center gap-2.5">
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

          <button
            type="button"
            onClick={() =>
              navigate('/agent/chat', {
                state: { seed: `Tell me about: ${card.title}`, cardId: card.id },
              })
            }
            className="ml-auto inline-flex h-10 items-center gap-1.5 rounded-full px-3 text-sm font-medium text-[var(--text-muted)] transition-colors hover:text-[var(--text)]"
          >
            Discuss with Vigil
            <ArrowUpRight size={14} strokeWidth={1.5} />
          </button>
        </div>
      </NotificationCard>
    </motion.div>
  );
}

export default TaskCard;
