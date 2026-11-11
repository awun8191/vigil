import { AppWindow, ArrowUpRight, CalendarDays, Eye, Gauge, Hash, Mail, PinOff, Plus } from 'lucide-react';
import { motion, useReducedMotion } from 'framer-motion';
import type { ComponentType } from 'react';
import { useNavigate } from 'react-router-dom';
import { useConnectors, useLimits } from '@/queries/settings';
import { usePinnedWidgets } from '@/components/chat/ArtifactCard';

type IconType = ComponentType<{ size?: number; strokeWidth?: number; className?: string }>;

/** A quiet counterpoint to the briefing spring: slide, settle, then stay still. */
function slideIn(reduced: boolean, delay: number) {
  return {
    initial: reduced ? { opacity: 0 } : { opacity: 0, x: 18 },
    animate: { opacity: 1, x: 0 },
    transition: { duration: reduced ? 0.2 : 0.45, delay, ease: 'easeOut' as const },
  };
}

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

/** Backend connector status to a single indicator shade: status is verbal, the dot just marks it. */
function toneFor(status: string): string {
  const s = status.toLowerCase();
  if (s === 'active' || s === 'connected' || s === 'ok') return 'var(--text)';
  if (s === 'error' || s === 'revoked' || s === 'expired') return 'color-mix(in srgb, var(--text) 90%, transparent)';
  return 'color-mix(in srgb, var(--text) 30%, transparent)';
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-lg bg-[color-mix(in_srgb,var(--text)_6%,transparent)] px-3 py-2.5">
      <div className="truncate text-[11px] text-[var(--text-muted)]">{label}</div>
      <div className="mt-0.5 truncate text-[17px] font-semibold tracking-heading">{value}</div>
    </div>
  );
}

export function WidgetsRail() {
  const navigate = useNavigate();
  const connectors = useConnectors();
  const limits = useLimits();
  const { widgets, unpinWidget } = usePinnedWidgets();
  const reduced = useReducedMotion() ?? false;

  const accounts = connectors.data ?? [];

  return (
    <aside aria-label="Widgets" className="flex flex-col gap-4">
      {widgets.length > 0 ? (
        <motion.section {...slideIn(reduced, 0.08)} className="card rounded-squircle-card p-5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-medium">
              <AppWindow size={16} strokeWidth={1.5} className="text-[var(--accent)]" />
              Pinned Micro-Apps
            </div>
            <span className="rounded-full bg-[var(--accent-soft)] px-2 py-0.5 font-mono text-[11px] font-semibold text-[var(--accent)]">
              {widgets.length}
            </span>
          </div>

          <div className="mt-3 space-y-3">
            {widgets.map((widget) => (
              <div
                key={widget.id}
                className="overflow-hidden rounded-[var(--r-inner)] border border-[var(--border)] bg-[color-mix(in_srgb,var(--surface-solid)_94%,transparent)] p-3"
              >
                <div className="flex items-center justify-between gap-2 pb-2">
                  <span className="truncate font-mono text-[12px] font-semibold text-[var(--text)]">
                    {widget.name}
                  </span>
                  <button
                    type="button"
                    onClick={() => unpinWidget(widget.id)}
                    aria-label={`Unpin ${widget.name}`}
                    title="Unpin widget"
                    className="cursor-pointer text-[var(--text-muted)] transition-colors hover:text-rose-400"
                  >
                    <PinOff size={13} strokeWidth={1.75} />
                  </button>
                </div>
                {widget.format === 'html' ? (
                  <iframe
                    sandbox="allow-scripts"
                    srcDoc={widget.content}
                    title={widget.name}
                    className="h-44 w-full rounded-[var(--r-inner)] border border-[var(--border)] bg-white"
                  />
                ) : (
                  <pre className="max-h-36 overflow-auto rounded-[var(--r-inner)] bg-[color-mix(in_srgb,var(--surface-solid)_80%,transparent)] p-2 font-mono text-[11px] text-[var(--text-muted)]">
                    {widget.content || '// Content ready'}
                  </pre>
                )}
              </div>
            ))}
          </div>
        </motion.section>
      ) : null}
      <motion.section {...slideIn(reduced, 0.12)} className="card rounded-squircle-card p-5">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Gauge size={16} strokeWidth={1.5} className="text-[var(--text-muted)]" />
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
      </motion.section>

      <motion.section {...slideIn(reduced, 0.2)} className="card rounded-squircle-card p-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Eye size={16} strokeWidth={1.5} className="text-[var(--text-muted)]" />
            Vigil is watching
          </div>
          <button
            type="button"
            onClick={() => navigate('/agent/settings')}
            className="rounded-full px-2 py-1 text-xs font-medium text-[var(--text-muted)] transition-colors hover:text-[var(--text)]"
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
                  className="flex items-center justify-between rounded-lg bg-[color-mix(in_srgb,var(--text)_5%,transparent)] px-3.5 py-2.5"
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
          <p className="mt-3 rounded-lg bg-[color-mix(in_srgb,var(--text)_5%,transparent)] px-3.5 py-3 text-[13px] leading-relaxed text-[var(--text-muted)]">
            {connectors.isLoading ? 'Checking connectors...' : 'No connectors linked yet.'}
          </p>
        )}
      </motion.section>

      <motion.div {...slideIn(reduced, 0.28)}>
        <button
          type="button"
          onClick={() => navigate('/agent/chat')}
          className="btn btn-ghost h-12 w-full"
        >
          <Plus size={16} strokeWidth={1.5} />
          Ask Vigil for something
          <ArrowUpRight size={14} strokeWidth={1.5} />
        </button>
      </motion.div>
    </aside>
  );
}

export default WidgetsRail;
