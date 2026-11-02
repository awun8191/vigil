export interface SpringConfig {
  stiffness: number;
  damping: number;
  mass: number;
}

export interface SpringState {
  x: number;
  v: number;
}

/** Semi-implicit Euler is stable only under a small step, so a long frame is split into substeps. */
const MAX_STEP = 1 / 240;

export function stepSpring(
  state: SpringState,
  target: number,
  config: SpringConfig,
  dt: number,
): void {
  const { stiffness, damping, mass } = config;
  if (mass <= 0 || dt <= 0 || !Number.isFinite(dt)) return;

  let remaining = Math.min(dt, 0.1);
  while (remaining > 0) {
    const h = Math.min(remaining, MAX_STEP);
    const accel = (-stiffness * (state.x - target) - damping * state.v) / mass;
    state.v += accel * h;
    state.x += state.v * h;
    remaining -= h;
  }
}
