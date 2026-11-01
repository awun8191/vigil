import { groupCards } from '@/lib/format';
import type { Card } from '@/types';
import { CardItem } from './CardItem';

interface CardStackProps {
  cards: Card[];
  onError: (message: string) => void;
}

export function CardStack({ cards, onError }: CardStackProps) {
  const groups = groupCards(cards);
  const twoColumn = cards.length > 6;

  return (
    <div className="space-y-8">
      {groups.map((group) => (
        <section key={group.key} aria-label={group.key}>
          <h2 className="mb-3 px-1 text-[12px] font-medium tracking-[0.04em] uppercase text-ink-3">
            {groupLabel(group.key)}
          </h2>
          <div className={twoColumn ? 'grid gap-3 xl:grid-cols-2' : 'space-y-3'}>
            {group.cards.map((card) => (
              <CardItem key={card.id} card={card} onError={onError} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function groupLabel(key: string): string {
  switch (key) {
    case 'today':
      return 'Today';
    case 'tomorrow':
      return 'Tomorrow';
    case 'later':
      return 'Later';
    default:
      return 'No date';
  }
}
