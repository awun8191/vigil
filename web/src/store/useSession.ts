import { create } from 'zustand';
import type { AuthMe, PairingResponse } from '@/types';
import { getBearerToken, setBearerToken } from '@/lib/api';

/**
 * Session state.
 *
 * The bearer token is held in memory for the session and persisted in IndexedDB so a reload does
 * not force re-pairing. Never localStorage (readable by any script on the origin), never a query
 * string (lands in access logs and Referer), never a cookie in v1 (needs CSRF handling).
 */

const DB_NAME = 'vigil';
const STORE = 'auth';
const RECORD_KEY = 'device';

interface StoredSession {
  token: string;
  deviceId: string;
  deviceName: string;
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error('IndexedDB unavailable'));
  });
}

async function readStoredSession(): Promise<StoredSession | null> {
  try {
    const db = await openDb();
    return await new Promise<StoredSession | null>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly');
      const request = tx.objectStore(STORE).get(RECORD_KEY);
      request.onsuccess = () => resolve((request.result as StoredSession | undefined) ?? null);
      request.onerror = () => reject(request.error);
    });
  } catch {
    return null;
  }
}

async function writeStoredSession(value: StoredSession): Promise<void> {
  try {
    const db = await openDb();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).put(value, RECORD_KEY);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } catch {
    // Storage is a convenience. A failure here only costs a re-pair on reload.
  }
}

async function clearStoredSession(): Promise<void> {
  try {
    const db = await openDb();
    await new Promise<void>((resolve) => {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).delete(RECORD_KEY);
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
    });
  } catch {
    // Nothing to clean up.
  }
}

export type SessionStatus = 'loading' | 'unauthed' | 'authed';

interface SessionState {
  status: SessionStatus;
  device: AuthMe | null;
  hydrate: (me: () => Promise<AuthMe>) => Promise<void>;
  signIn: (pairing: PairingResponse, me: () => Promise<AuthMe>) => Promise<void>;
  signOut: () => Promise<void>;
  expire: () => void;
}

export const useSession = create<SessionState>((set) => ({
  status: 'loading',
  device: null,

  async hydrate(me) {
    const stored = await readStoredSession();
    if (!stored) {
      setBearerToken(null);
      set({ status: 'unauthed', device: null });
      return;
    }

    setBearerToken(stored.token);
    try {
      const device = await me();
      set({ status: 'authed', device });
    } catch {
      // A stored token that no longer works is worthless. Clear it rather than retrying forever.
      await clearStoredSession();
      setBearerToken(null);
      set({ status: 'unauthed', device: null });
    }
  },

  async signIn(pairing, me) {
    setBearerToken(pairing.device_token);
    await writeStoredSession({
      token: pairing.device_token,
      deviceId: pairing.device_id,
      deviceName: pairing.device_name,
    });

    try {
      const device = await me();
      set({ status: 'authed', device });
    } catch {
      set({
        status: 'authed',
        device: {
          device_id: pairing.device_id,
          device_name: pairing.device_name,
          user_id: pairing.user_id,
          user_display_name: '',
          created_at: new Date().toISOString(),
          last_seen_at: new Date().toISOString(),
        },
      });
    }
  },

  async signOut() {
    await clearStoredSession();
    setBearerToken(null);
    set({ status: 'unauthed', device: null });
  },

  expire() {
    void clearStoredSession();
    setBearerToken(null);
    set({ status: 'unauthed', device: null });
  },
}));

export function currentToken(): string | null {
  return getBearerToken();
}
