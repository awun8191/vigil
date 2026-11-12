export interface TaskListItem {
  id: string;
  label: string;
  done: boolean;
}

interface TaskListProps {
  items: TaskListItem[];
}

export function TaskList({ items }: TaskListProps) {
  if (items.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">No tasks yet.</p>;
  }
  return (
    <ul className="space-y-1.5">
      {items.map((item) => (
        <li key={item.id} className="flex items-start gap-2 text-[13px]">
          <span
            aria-hidden="true"
            className={
              item.done
                ? 'mt-1.5 h-2 w-2 shrink-0 rounded-full bg-[var(--accent)]'
                : 'mt-1.5 h-2 w-2 shrink-0 rounded-full border border-[var(--text-muted)]'
            }
          />
          <span className={item.done ? 'text-[var(--text-muted)] line-through' : undefined}>{item.label}</span>
        </li>
      ))}
    </ul>
  );
}

export default TaskList;
