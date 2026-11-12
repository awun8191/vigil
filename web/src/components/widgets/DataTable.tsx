interface DataTableProps {
  columns: string[];
  rows: string[][];
}

export function DataTable({ columns, rows }: DataTableProps) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column}
                scope="col"
                className="border-b border-[var(--border)] px-2 py-1.5 text-left font-medium text-[var(--text-muted)]"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {columns.map((column, colIndex) => (
                <td key={`${column}-${colIndex}`} className="border-b border-[var(--border)] px-2 py-1.5 align-top">
                  {row[colIndex] ?? ''}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 ? (
        <p className="px-2 py-2 text-[13px] text-[var(--text-muted)]">No rows yet.</p>
      ) : null}
    </div>
  );
}

export default DataTable;
