function hash(index: number, seed: number): number {
  let h = Math.imul(index ^ seed, 2654435761);
  h ^= h >>> 15;
  h = Math.imul(h, 2246822507);
  h ^= h >>> 13;
  return (h >>> 0) / 4294967296;
}

/**
 * Smooth one-dimensional value noise in [0, 1). Cheaper than a PRNG per sample and it never
 * repeats in a way the eye can catch, which is all a simulated voice needs.
 */
export function valueNoise1D(seed: number): (x: number) => number {
  return (x: number) => {
    const i = Math.floor(x);
    const f = x - i;
    const a = hash(i, seed);
    const b = hash(i + 1, seed);
    const u = f * f * (3 - 2 * f);
    return a + (b - a) * u;
  };
}
