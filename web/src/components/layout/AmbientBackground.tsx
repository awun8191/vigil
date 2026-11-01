/**
 * The ambient surface.
 *
 * Two static radial gradients on a single fixed pseudo-element inside `.ambient-mesh`, blurred
 * once. The filter is never animated: animating `filter: blur()` forces a full-viewport filter
 * pass per frame and is the standard cause of 20fps scrolling on mid-range Android and on
 * Firefox under Wayland.
 */
export function AmbientBackground() {
  return <div className="ambient-mesh" aria-hidden="true" />;
}
