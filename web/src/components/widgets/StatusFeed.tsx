export interface StatusFeedEntry {
  ts: string;
  text: string;
}

interface StatusFeedProps {
  entries: StatusFeedEntry[];
}

export function StatusFeed({ entries }: StatusFeedProps) {
  if (entries.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">No updates yet.</p>;
  }
  return (
    <ul className="space-y-2">
      {entries.map((entry, index) => (
        <li key={`${entry.ts}-${index}`} className="text-[13px] leading-relaxed">
          <div className="font-mono text-[11px] text-[var(--text-muted)]">{entry.ts}</div>
          <div className="mt-0.5">{entry.text}</div>
        </li>
      ))}
    </ul>
  );
}

export default StatusFeed;
