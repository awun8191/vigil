interface SparkMarkProps {
  size?: number;
  className?: string;
}

/**
 * The Vigil brand mark.
 *
 * Inline SVG, deliberately not the `✦` character: a Unicode glyph renders at different sizes and
 * baselines per font stack and risks tofu on systems without coverage.
 */
export function SparkMark({ size = 14, className }: SparkMarkProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      focusable="false"
      className={className}
    >
      <path
        d="M12 1.5c.9 6.3 4.2 9.6 10.5 10.5-6.3.9-9.6 4.2-10.5 10.5-.9-6.3-4.2-9.6-10.5-10.5C7.8 11.1 11.1 7.8 12 1.5Z"
        fill="currentColor"
      />
    </svg>
  );
}
