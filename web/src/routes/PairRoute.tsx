import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, Smartphone } from 'lucide-react';
import { api, IS_MOCK } from '@/lib/api';
import { userMessageOf } from '@/lib/errors';
import { formatPairingCode, platformDeviceName } from '@/lib/format';
import { SparkMark } from '@/components/layout/SparkMark';
import { useSession } from '@/store/useSession';

/**
 * The pairing gate.
 *
 * Renders before the app shell whenever no token is available. Without this route a first-run user
 * has no surface at all: Settings lives behind the dock, and the dock requires auth.
 */
export default function PairRoute() {
  const navigate = useNavigate();
  const signIn = useSession((state) => state.signIn);

  const [code, setCode] = useState('');
  const [deviceName, setDeviceName] = useState(() => platformDeviceName());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = code.length >= 6 && deviceName.trim().length > 0 && !submitting;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;

    setSubmitting(true);
    setError(null);

    try {
      const pairing = await api.pair(code, deviceName.trim());
      await signIn(pairing, () => api.me());
      navigate('/', { replace: true });
    } catch (cause) {
      setError(
        cause instanceof Error && cause.name === 'ApiError' && 'status' in cause && cause.status === 401
          ? 'That code was not recognised. It expires 10 minutes after boot.'
          : userMessageOf(cause),
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="flex min-h-dvh items-center justify-center px-5 py-16">
      <div className="w-full max-w-[420px]">
        <div className="mb-8 flex items-center gap-2">
          <span className="text-[20px] font-bold tracking-[-0.02em]">Vigil</span>
          <SparkMark size={11} className="text-accent" />
        </div>

        <div
          className="rounded-[var(--r-card)] border border-hairline bg-card p-6"
          style={{ boxShadow: 'var(--shadow-card)' }}
        >
          <h1 className="text-[22px] font-semibold tracking-[-0.015em]">Pair this device</h1>
          <p className="mt-1.5 text-[14px] leading-relaxed text-ink-3">
            Enter the code printed by the Vigil backend when it started.
          </p>

          <form onSubmit={submit} className="mt-6 space-y-4">
            <div>
              <label htmlFor="pairing-code" className="mb-1.5 block text-[13px] font-medium text-ink-2">
                Pairing code
              </label>
              <input
                id="pairing-code"
                inputMode="text"
                autoComplete="one-time-code"
                autoCapitalize="characters"
                spellCheck={false}
                value={code}
                onChange={(event) => setCode(formatPairingCode(event.target.value))}
                placeholder="VIG-000000"
                className="tabular w-full rounded-[var(--r-control)] border border-hairline-strong bg-raised px-3.5 py-2.5 font-mono text-[15px] tracking-[0.08em] text-ink outline-none placeholder:text-ink-3/70 focus-visible:border-accent"
              />
            </div>

            <div>
              <label htmlFor="device-name" className="mb-1.5 block text-[13px] font-medium text-ink-2">
                Device name
              </label>
              <input
                id="device-name"
                value={deviceName}
                onChange={(event) => setDeviceName(event.target.value)}
                maxLength={100}
                className="w-full rounded-[var(--r-control)] border border-hairline-strong bg-raised px-3.5 py-2.5 text-[15px] text-ink outline-none placeholder:text-ink-3/70 focus-visible:border-accent"
              />
            </div>

            {error ? (
              <p role="alert" className="text-[13px] leading-relaxed text-critical">
                {error}
              </p>
            ) : null}

            <button
              type="submit"
              disabled={!canSubmit}
              className="inline-flex w-full items-center justify-center gap-2 rounded-[var(--r-pill)] bg-accent px-4 py-2.5 text-[14px] font-medium text-inverse transition-colors duration-[var(--dur-base)] ease-[var(--ease-spring)] hover:bg-accent-hover disabled:opacity-40"
            >
              {submitting ? 'Pairing...' : 'Pair device'}
              <ArrowRight size={16} strokeWidth={1.75} aria-hidden="true" />
            </button>
          </form>

          <p className="mt-5 flex items-start gap-2 text-[12px] leading-relaxed text-ink-3">
            <Smartphone size={14} strokeWidth={1.5} aria-hidden="true" className="mt-0.5 shrink-0" />
            The code is single-use and rotates on every backend restart.
          </p>
        </div>

        {IS_MOCK ? (
          <p className="mt-4 text-center text-[12px] text-ink-3">
            Simulation mode is on. Any code will be accepted.
          </p>
        ) : null}
      </div>
    </main>
  );
}
