import { ArrowUpRight, CalendarDays, Eye, Gauge, Hash, Mail, Plus } from 'lucide-react';
import type { ComponentType } from 'react';
import { useNavigate } from 'react-router-dom';
import { useConnectors, useLimits } from '@/queries/settings';

type IconType = ComponentType<{ size?: number; strokeWidth?: number; className?: string }>;

const ICONS: Record<string, IconType> = {
  mail: Mail,
  gmail: Mail,
  calendar: CalendarDays,
  slack: Hash,
  drive: Gauge,
};

function iconFor(connectorId: string): IconType {
  const key = Object.keys(ICONS).find((k) => connectorId.toLowerCase().includes(k));
  return key ? (ICONS[key] as IconType) : Gauge;
}

/** Backend connector status to a single indicator colour. */
function toneFor(status: string): string {
  const s = status.toLowerCase();
  if (s === 'active' || s === 'connected' || s === 'ok') return '#34d399';
  if (s === 'error' || s === 'revoked' || s === 'expired') return '#f87171';
  return '#fbbf24';
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-2xl bg-[color-mix(in_srgb,var(--text)_6%,transparent)] px-3 py-2.5">
      <div className="truncate text-[11px] text-[var(--text-muted)]">{label}</div>
      <div className="mt-0.5 truncate text-[17px] font-semibold tracking-heading">{value}</div>
    </div>
  );
}

export function WidgetsRail() {
  const navigate = useNavigate();
  const connectors = useConnectors();
  const limits = useLimits();

  const accounts = connectors.data ?? [];

  return (
    <aside aria-label="Widgets" className="flex flex-col gap-4">
      <section className="card animate-rise rounded-[28px] p-5" style={{ animationDelay: '0.1s' }}>
        <div className="flex items-center gap-2 text-sm font-medium">
          <Gauge size={16} strokeWidth={1.5} className="text-[var(--accent)]" />
          Limits
        </div>
        <div className="mt-4 grid grid-cols-3 gap-2">
          <Stat
            label="Steps"
            value={limits.data ? String(limits.data.max_steps_per_run) : '—'}
          />
          <Stat
            label="Cost"
            value={limits.data ? `$${limits.data.max_cost_usd_per_run.toFixed(2)}` : '—'}
          />
          <Stat
            label="Timeout"
            value={limits.data ? `${Math.round(limits.data.run_timeout_seconds / 60)}m` : '—'}
          />
        </div>
      </section>

      <section className="card animate-rise rounded-[28px] p-5" style={{ animationDelay: '0.18s' }}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Eye size={16} strokeWidth={1.5} className="text-[var(--accent)]" />
            Vigil is watching
          </div>
          <button
            type="button"
            onClick={() => navigate('/settings')}
            className="rounded-full px-2 py-1 text-xs font-medium text-[var(--text-muted)] transition-colors hover:text-[var(--text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            Manage
          </button>
        </div>

        {accounts.length > 0 ? (
          <ul className="mt-3 space-y-1.5">
            {accounts.map((account) => {
              const Icon = iconFor(account.connector_id);
              return (
                <li
                  key={account.id}
                  className="flex items-center justify-between rounded-2xl bg-[color-mix(in_srgb,var(--text)_5%,transparent)] px-3.5 py-2.5"
                >
                  <span className="flex min-w-0 items-center gap-2.5 text-sm">
                    <Icon
                      size={16}
                      strokeWidth={1.5}
                      className="shrink-0 text-[var(--text-muted)]"
                    />
                    <span className="truncate">{account.account_label}</span>
                  </span>
                  <span
                    className="h-2 w-2 shrink-0 rounded-full"
                    style={{ background: toneFor(account.status) }}
                    role="img"
                    aria-label={account.status.replace(/_/g, ' ')}
                  />
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="mt-3 rounded-2xl bg-[color-mix(in_srgb,var(--text)_5%,transparent)] px-3.5 py-3 text-[13px] leading-relaxed text-[var(--text-muted)]">
            {connectors.isLoading ? 'Checking connectors...' : 'No connectors linked yet.'}
          </p>
        )}
      </section>

      <button
        type="button"
        onClick={() => navigate('/chat')}
        className="btn btn-ghost animate-rise h-12 w-full"
        style={{ animationDelay: '0.26s' }}
      >
        <Plus size={16} strokeWidth={1.5} />
        Ask Vigil for something
        <ArrowUpRight size={14} strokeWidth={1.5} />
      </button>
    </aside>
  );
}

export default WidgetsRail;
