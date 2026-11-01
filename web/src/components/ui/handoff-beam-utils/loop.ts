import { useEffect, useRef, type RefObject } from 'react';

export interface LoopOptions {
  /** 0 means uncapped. A cap also lowers the cost when the effect is decorative. */
  fpsCap?: number;
}

/**
 * Runs `frame(dt)` on requestAnimationFrame, but only while the element is on screen and the tab
 * is visible. Idle tabs and off-screen hosts cost nothing.
 */
export function useVisibleLoop(
  ref: RefObject<HTMLElement | null>,
  frame: (dt: number) => void,
  options: LoopOptions = {},
): void {
  const frameRef = useRef(frame);
  frameRef.current = frame;

  const capRef = useRef(options.fpsCap ?? 0);
  capRef.current = options.fpsCap ?? 0;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    let raf = 0;
    let last = 0;
    let visible = true;

    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);

      if (!visible || document.hidden) {
        last = now;
        return;
      }

      const cap = capRef.current;
      if (cap > 0 && now - last < 1000 / cap) return;

      const dt = last === 0 ? 1 / 60 : Math.min(0.1, (now - last) / 1000);
      last = now;
      frameRef.current(dt);
    };

    const observer =
      typeof IntersectionObserver === 'undefined'
        ? null
        : new IntersectionObserver((entries) => {
            visible = entries[0]?.isIntersecting ?? true;
          });

    observer?.observe(el);
    raf = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(raf);
      observer?.disconnect();
    };
  }, [ref]);
}
