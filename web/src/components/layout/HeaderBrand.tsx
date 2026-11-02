import { Link } from 'react-router-dom';

export function HeaderBrand() {
  return (
    <Link
      to="/"
      aria-label="Vigil, go to home"
      className="fixed left-6 top-6 z-30 flex select-none items-center gap-1.5 text-[15px] font-semibold tracking-heading transition-opacity hover:opacity-70 md:left-8 md:top-7"
    >
      <span>Vigil</span>
    </Link>
  );
}

export default HeaderBrand;
