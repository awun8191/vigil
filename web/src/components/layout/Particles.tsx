import { useMemo } from 'react';
import SparkMark from './SparkMark';

function mulberry32(seed: number) {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Slow-drifting four-point sparks, the quiet counterpart to a hero's floating glyphs. */
export function Particles({ count = 34 }: { count?: number }) {
  const sparks = useMemo(() => {
    const r = mulberry32(7);
    return Array.from({ length: count }, (_, id) => ({
      id,
      x: r() * 100,
      y: r() * 100,
      size: 5 + r() * 9,
      o: 0.16 + r() * 0.4,
      dur: 14 + r() * 18,
      delay: -r() * 30,
    }));
  }, [count]);

  return (
    <div aria-hidden className="absolute inset-0">
      {sparks.map((s) => (
        <span
          key={s.id}
          className="spark"
          style={{
            left: `${s.x}%`,
            top: `${s.y}%`,
            color: 'var(--spark)',
            ['--o' as string]: String(s.o),
            animationDuration: `${s.dur}s`,
            animationDelay: `${s.delay}s`,
          }}
        >
          <SparkMark size={s.size} />
        </span>
      ))}
    </div>
  );
}

export default Particles;
