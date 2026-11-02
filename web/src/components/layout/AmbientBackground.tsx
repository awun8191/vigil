const GRAIN =
  "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='180' height='180'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix type='saturate' values='0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>\")";

const cloud = (color: string) => `radial-gradient(ellipse at center, ${color}, transparent 68%)`;

export function AmbientBackground() {
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      {/* Cloud banks */}
      <div
        className="animate-drift absolute -left-[18%] -top-[28%] h-[72vh] w-[78vw] blur-3xl"
        style={{ background: cloud('var(--cloud-1)') }}
      />
      <div
        className="animate-drift absolute -right-[22%] top-[2%] h-[78vh] w-[72vw] blur-3xl"
        style={{ background: cloud('var(--cloud-2)'), animationDelay: '-6s' }}
      />
      <div
        className="animate-drift absolute bottom-[-22%] left-[12%] h-[60vh] w-[86vw] blur-3xl"
        style={{ background: cloud('var(--cloud-3)'), animationDelay: '-12s' }}
      />

      {/* Horizon fade, grounding the lower third */}
      <div
        className="absolute inset-x-0 bottom-0 h-[48vh]"
        style={{ background: 'linear-gradient(to top, var(--horizon), transparent)' }}
      />

      {/* Film grain */}
      <div
        className="absolute inset-0"
        style={{ backgroundImage: GRAIN, opacity: 'var(--grain-opacity)', mixBlendMode: 'overlay' }}
      />
    </div>
  );
}

export default AmbientBackground;
