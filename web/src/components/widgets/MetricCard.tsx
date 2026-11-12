interface MetricCardProps {
  value: string;
  delta?: string;
  caption?: string;
}

export function MetricCard({ value, delta, caption }: MetricCardProps) {
  return (
    <div>
      <div className="truncate text-[22px] font-semibold tracking-heading">{value}</div>
      {delta ? <div className="mt-0.5 truncate text-[12px] text-[var(--text-muted)]">{delta}</div> : null}
      {caption ? (
        <div className="mt-1 text-[13px] leading-relaxed text-[var(--text-muted)]">{caption}</div>
      ) : null}
    </div>
  );
}

export default MetricCard;
