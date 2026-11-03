import { Link } from 'react-router-dom';
import EyeTracker from '@/components/landing/EyeTracker';

/**
 * Fixed brand lockup on the agent pages: the wordmark with the watching mark to its
 * right. The mark's pointer-tracking is page-wide (`within="body"`); pokes open its
 * bubble without triggering the link behind it.
 */
export function HeaderBrand() {
  return (
    <Link
      to="/agent"
      aria-label="Vigil, go to home"
      className="fixed left-6 top-6 z-30 flex select-none items-center gap-2 text-[15px] font-semibold tracking-heading transition-opacity hover:opacity-70 md:left-8 md:top-7"
    >
      <span>Vigil</span>
      <span className="eyt-inline flex items-center" onClick={(event) => event.stopPropagation()}>
        <EyeTracker size={36} shape="Cube" eyes="Slant" eyeScale={1.15} follow={20} bounce={40} within="body" />
      </span>
    </Link>
  );
}

export default HeaderBrand;
