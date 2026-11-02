import { useSyncExternalStore } from 'react';
import type { BjorkTone } from './palette';

/**
 * The document carries the tone, not React: `data-theme` is written by the boot script before
 * first paint. Subscribing to the attribute keeps the beam in step with a theme flip without
 * coupling it to the app's store.
 */
function subscribe(onChange: () => void) {
  if (typeof MutationObserver === 'undefined') return () => {};
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme'],
  });
  return () => observer.disconnect();
}

function getSnapshot(): BjorkTone {
  if (typeof document === 'undefined') return 'dark';
  return document.documentElement.dataset['theme'] === 'light' ? 'light' : 'dark';
}

const getServerSnapshot = (): BjorkTone => 'dark';

export function useBjorkTone(tone?: BjorkTone): BjorkTone {
  const observed = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return tone ?? observed;
}
