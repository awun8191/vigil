import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowUpRight, Check, Loader2, Square } from 'lucide-react';
import { motion, useReducedMotion } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api';
import { userMessageOf } from '@/lib/errors';
import { relativeTime } from '@/lib/format';
import { releaseIdempotencyKey } from '@/lib/idempotency';
import { runKey, stepLabel, useCancelRun, useDefaultAgentConfig, useRun } from '@/queries/runs';
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
  const cancelRun = useCancelRun();
  const defaultConfig = useDefaultAgentConfig();
  const reduced = useReducedMotion();

  /** Set when a card-run action POSTs successfully; drives the inline progress strip. */
  const [runId, setRunId] = useState<string | null>(null);
  const subscription = useRun(runId);

  const source = sourceFor(card.type);
  const due = relativeTime(card.due_at);

  const runAction = useMutation({
    mutationFn: ({
      action,
      cardId,
      scope,
    }: {
      action: CardAction;
      cardId: string;
      scope: string;
    }) => {
      // POST /runs requires config_id. Card actions carry their own config when the backend
      // attaches one; otherwise the pinned server-side default config runs the card.
      const payloadConfigId =
        typeof action.payload['config_id'] === 'string' ? action.payload['config_id'] : null;
      const configId = payloadConfigId ?? defaultConfig.data?.id;
      const prompt = typeof action.payload['initial_prompt'] === 'string' ? action.payload['initial_prompt'] : null;
      if (!configId) {
        return Promise.reject(
          new Error('The agent configuration is not ready yet. Try again in a moment.'),
        );
      }
      return api.createRun(
        {
          config_id: configId,
          card_id: cardId,
          initial_prompt: prompt,
        },
        scope,
      );
    },

    onSuccess: (run) => {
      // The run id is the hook into the events stream, keep it, never drop it.
      setRunId(run.id);
      queryClient.setQueryData(runKey(run.id), run);
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
        runAction.mutate({ action, cardId: card.id, scope });
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
    (action.action_type === 'run' &&
      (typeof action.payload['config_id'] === 'string' || defaultConfig.data !== undefined)) ||
    action.action_type === 'dismiss' ||
    action.action_type === 'reply';

  const [primary, ...rest] = card.actions;
  const isBlocking = card.status === 'awaiting_approval';

  const statusLabel = isBlocking ? 'Waiting on you' : card.due_at ? 'Gentle reminder' : 'For the record';
  const finished = subscription.run ? ['completed', 'failed'].includes(subscription.run.status) : false;
  const activeVerb = card.verb || primary?.verb || (primary ? (primary.action_type === 'reply' ? 'Reply' : primary.action_type === 'dismiss' ? 'Dismiss' : 'Review') : null);

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
        urgency={card.urgency}
        proactivityTag={card.proactivity_tag}
        verb={activeVerb}
        pillText={card.summary || card.title}
        onVerbClick={() => {
          if (primary && isKnown(primary)) {
            dispatch(primary);
          }
        }}
        onNotchClick={() => {
          navigate('/agent/chat', {
            state: { seed: `Tell me about: ${card.title}`, cardId: card.id },
          });
        }}
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

        {runId && !finished ? (
          <div className="mt-4 rounded-[var(--r-inner)] border border-[var(--border)] px-3 py-2.5">
            <div className="flex items-center gap-2 text-[13px] font-medium">
              <Loader2 size={14} className="animate-spin text-[var(--text-muted)]" strokeWidth={1.75} />
              <span className="text-[var(--text-muted)]">
                {subscription.run?.status === 'queued' ? 'Starting' : 'Running'}
              </span>
              <button
                type="button"
                onClick={() => cancelRun.mutate({ runId, scope: `run:cancel:${runId}` })}
                disabled={cancelRun.isPending}
                className="ml-auto inline-flex items-center gap-1 text-[12px] text-[var(--text-muted)] transition-colors hover:text-[var(--text)]"
              >
                <Square size={11} strokeWidth={1.75} />
                Stop
              </button>
            </div>

            {subscription.steps.length > 0 ? (
              <ul className="mt-2 space-y-1.5">
                {subscription.steps.slice(-3).map((step) => (
                  <li key={step.step_number} className="flex items-center gap-2 text-[13px] text-[var(--text-muted)]">
                    <span className="h-1.5 w-1.5 rounded-full bg-[color-mix(in_srgb,var(--text)_25%,transparent)]" />
                    {stepLabel(step)}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}

        {runId && finished && subscription.run ? (
          <p className="mt-4 text-[13px] text-[var(--text-muted)]">
            {subscription.run.status === 'completed' ? 'Ran to completion.' : 'That run failed.'}
          </p>
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
              {primary.verb && primary.verb !== primary.label ? (
                <span>
                  <span className="font-semibold">{primary.verb}</span>
                  <span className="opacity-75 font-normal"> · {primary.label}</span>
                </span>
              ) : (
                primary.label
              )}
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
              {action.verb && action.verb !== action.label ? (
                <span>
                  <span className="font-semibold">{action.verb}</span>
                  <span className="opacity-75 font-normal"> · {action.label}</span>
                </span>
              ) : (
                action.label
              )}
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
