/** Announces phase changes to assistive tech without putting them in the visual flow. */
export function LiveRegion({ message }: { message: string }) {
  return (
    <span className="sr-only" role="status" aria-live="polite" aria-atomic="true">
      {message}
    </span>
  );
}
