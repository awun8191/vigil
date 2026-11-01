import { Link } from 'react-router-dom';
import { SparkMark } from './SparkMark';

export function BrandMark() {
  return (
    <Link
      to="/"
      aria-label="Vigil, go to home"
      className="inline-flex items-center gap-2 rounded-control px-1 py-1 text-ink transition-opacity duration-[var(--dur-fast)] ease-[var(--ease-spring)] hover:opacity-70"
    >
      <span className="text-[20px] font-bold leading-none tracking-[-0.02em]">Vigil</span>
      <SparkMark size={11} className="text-accent" />
    </Link>
  );
}
