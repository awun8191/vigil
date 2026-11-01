import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { releaseIdempotencyKey } from '@/lib/idempotency';
import type { Card, CardFeedbackRequest, CardListResponse } from '@/types';

export const CARDS_KEY = ['cards'] as const;

export function useCards() {
  return useQuery<CardListResponse>({
    queryKey: CARDS_KEY,
    queryFn: () => api.listCards({ limit: 50 }),
    // There is no /cards/stream on the backend. Polling is the honest mechanism.
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    staleTime: 15_000,
  });
}

export function useDismissCard() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ cardId, scope }: { cardId: string; scope: string }) =>
      api.dismissCard(cardId, scope),

    onMutate: async ({ cardId }) => {
      await queryClient.cancelQueries({ queryKey: CARDS_KEY });
      const previous = queryClient.getQueryData<CardListResponse>(CARDS_KEY);

      queryClient.setQueryData<CardListResponse>(CARDS_KEY, (current) =>
        current
          ? {
              ...current,
              items: current.items.filter((card) => card.id !== cardId),
              total: Math.max(0, current.total - 1),
            }
          : current,
      );

      return { previous };
    },

    onError: (_error, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(CARDS_KEY, context.previous);
    },

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: CARDS_KEY });
    },
  });
}

export function useCardFeedback() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      cardId,
      body,
      scope,
    }: {
      cardId: string;
      body: CardFeedbackRequest;
      scope: string;
    }) => api.cardFeedback(cardId, body, scope),

    onSuccess: (card: Card) => {
      queryClient.setQueryData<CardListResponse>(CARDS_KEY, (current) =>
        current
          ? { ...current, items: current.items.map((c) => (c.id === card.id ? card : c)) }
          : current,
      );
    },

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
    },
  });
}
