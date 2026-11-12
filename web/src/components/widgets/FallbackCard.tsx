interface FallbackCardProps {
  title: string;
  updatedAt: string;
}

export function FallbackCard({ title, updatedAt }: FallbackCardProps) {
  return (
    <div>
      <div className="truncate pr-6 text-sm font-medium">{title}</div>
      <p className="mt-1 text-[13px] leading-relaxed text-[var(--text-muted)]">
        This panel needs a newer app version to render. Updated {updatedAt}.
      </p>
    </div>
  );
}

export default FallbackCard;
