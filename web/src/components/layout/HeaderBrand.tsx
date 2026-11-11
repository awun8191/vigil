import { Link } from 'react-router-dom';
import { VigilSpark } from '@/components/chat/MessageItem';

/**
 * Fixed brand lockup on the agent pages: the wordmark with the minimalist
 * 4-point Vigil spark mark to its right.
 */
export function HeaderBrand() {
  return (
    <Link
      to="/agent"
      aria-label="Vigil, go to home"
      className="fixed left-6 top-6 z-30 flex select-none items-center gap-2 text-[15px] font-semibold tracking-heading transition-opacity hover:opacity-70 md:left-8 md:top-7"
    >
      <span>Vigil</span>
      <span className="flex items-center text-[var(--accent)]" aria-hidden="true">
        <VigilSpark className="h-4 w-4" />
      </span>
    </Link>
  );
}

export default HeaderBrand;
