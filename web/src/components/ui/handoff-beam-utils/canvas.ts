import { useEffect, useState, type RefObject } from 'react';

export interface Size {
  width: number;
  height: number;
}

const EMPTY: Size = { width: 0, height: 0 };

/** Tracks an element's border-box in CSS pixels. */
export function useElementSize(ref: RefObject<HTMLElement | null>): Size {
  const [size, setSize] = useState<Size>(EMPTY);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const read = () => {
      const width = el.clientWidth;
      const height = el.clientHeight;
      setSize((prev) => (prev.width === width && prev.height === height ? prev : { width, height }));
    };

    read();
    const observer = new ResizeObserver(read);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);

  return size;
}
