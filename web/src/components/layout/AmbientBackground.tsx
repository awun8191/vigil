const GRAIN =
  "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.75' numOctaves='3' stitchTiles='stitch'/><feColorMatrix type='saturate' values='0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>\")";

/**
 * Living atmospheric cloudscape backdrop derived directly from Hark Pro production captures.
 * Light mode: Dreamy warm sand, ivory cream, soft blush peach, and cloud shadows.
 * Dark mode: Dusky charcoal, smoky slate, and deep plum night clouds.
 */
export function AmbientBackground() {
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden select-none">
      {/* Upper-left cloud drift */}
      <div
        className="animate-drift absolute -left-[15%] -top-[25%] h-[85vh] w-[85vw] rounded-full blur-[100px] transition-colors duration-700"
        style={{
          background:
            'radial-gradient(circle at 40% 40%, var(--cloud-1), transparent 70%)',
        }}
      />

      {/* Center atmospheric drift */}
      <div
        className="animate-drift absolute left-[20%] top-[15%] h-[75vh] w-[75vw] rounded-full blur-[120px] transition-colors duration-700"
        style={{
          background:
            'radial-gradient(circle at 50% 50%, var(--cloud-2), transparent 65%)',
          animationDelay: '-5s',
        }}
      />

      {/* Lower-right atmospheric drift */}
      <div
        className="animate-drift absolute -right-[15%] -bottom-[20%] h-[80vh] w-[80vw] rounded-full blur-[110px] transition-colors duration-700"
        style={{
          background:
            'radial-gradient(circle at 60% 60%, var(--cloud-3), transparent 65%)',
          animationDelay: '-10s',
        }}
      />

      {/* Atmospheric depth overlay */}
      <div
        className="absolute inset-0 opacity-40 mix-blend-soft-light"
        style={{
          background:
            'radial-gradient(ellipse at 50% 50%, transparent 40%, var(--horizon) 100%)',
        }}
      />

      {/* Fine tactile photographic grain */}
      <div
        className="absolute inset-0"
        style={{
          backgroundImage: GRAIN,
          opacity: 'var(--grain-opacity)',
          mixBlendMode: 'overlay',
        }}
      />
    </div>
  );
}

export default AmbientBackground;
