import type { ApiErrorEnvelope, FailureClass } from '@/types';

/**
 * The backend returns `{code, message, request_id, details}` for every failure, with the same
 * value echoed in the `X-Request-ID` header. Everything the UI shows the user comes from here.
 */
export class ApiError extends Error {
  readonly code: string;
  readonly requestId: string;
  readonly details: unknown;
  readonly status: number;

  constructor(status: number, envelope: Partial<ApiErrorEnvelope> | null) {
    super(envelope?.message ?? `Request failed with status ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.code = envelope?.code ?? 'UNKNOWN_ERROR';
    this.requestId = envelope?.request_id ?? '';
    this.details = envelope?.details ?? null;
  }

  get failureClass(): FailureClass {
    if (this.status === 409) return 'replayed';
    return 'rejected';
  }
}

/** Auth specifically, so the router can redirect exactly once and nowhere else. */
export class AuthError extends ApiError {
  constructor(status: number, envelope: Partial<ApiErrorEnvelope> | null) {
    super(status, envelope);
    this.name = 'AuthError';
  }
}

/** The request may or may not have reached the server. Always safe to retry with the same key. */
export class NetworkError extends Error {
  readonly failureClass: FailureClass = 'network';

  constructor(message: string) {
    super(message);
    this.name = 'NetworkError';
  }
}

export function isAuthError(error: unknown): error is AuthError {
  return error instanceof AuthError;
}

export function failureClassOf(error: unknown): FailureClass {
  if (error instanceof NetworkError) return 'network';
  if (error instanceof ApiError) return error.failureClass;
  return 'rejected';
}

/** Copy safe to render. Never leaks a stack trace or a raw backend payload. */
export function userMessageOf(error: unknown): string {
  if (error instanceof NetworkError) return "Vigil couldn't be reached. Check the connection.";
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return 'Something went wrong.';
}
