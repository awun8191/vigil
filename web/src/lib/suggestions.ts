import { useCards } from '@/queries/cards';

/**
 * Suggested prompts for an empty chat, derived from what Vigil is actually holding. Static
 * suggestion copy goes stale; live card titles do not.
 */
export function useCardTitles(limit: number): string[] {
  const { data } = useCards();
  return (data?.items ?? [])
    .filter((card) => card.status !== 'dismissed')
    .slice(0, limit)
    .map((card) => card.title);
}
