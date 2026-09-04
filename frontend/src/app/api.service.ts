import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, InjectionToken, inject } from '@angular/core';
import { Observable } from 'rxjs';

/**
 * Where the fieldproof API lives, without a trailing slash.
 *
 * Empty by default: the page calls `/api/...` on its own origin and the dev
 * server proxies that to the API (`proxy.conf.mjs`). That keeps a page served
 * over HTTPS from making a plain-HTTP request, which the browser would block
 * as mixed content. Override by providing this token in `app.config.ts`.
 */
export const API_BASE_URL = new InjectionToken<string>('API_BASE_URL', {
  providedIn: 'root',
  factory: () => '',
});

/** 200. The task's terms, as the landing page shows them. No verdict, no trail. */
export interface AssignmentDetails {
  id: string;
  business_name: string;
  participant_name: string;
  state: 'ASSIGNED' | 'EXPIRED' | 'FULFILLED';
  deadline_at: string;
  min_duration_s: number;
  report_deadline_s: number;
}

/** One reported position, as the browser sends it (spec.md §4, §8). */
export interface PingRequest {
  lat: number;
  lng: number;
  accuracy_m: number;
  /** ISO 8601 with an offset. A naive timestamp is a 422 on the server. */
  reported_at: string;
}

/** 202. The server's clock only; never a classification (ADR-0002). */
export interface PingAccepted {
  received_at: string;
}

/** 201. The id the page pings against for the rest of the visit. */
export interface VisitStarted {
  visit_id: string;
  started_at: string;
}

/** 200. The trail is sealed; the write-up is due by `report_deadline_at`. */
export interface VisitEnded {
  visit_id: string;
  ended_at: string;
  report_deadline_at: string;
}

/** 200. COMPLETED. No verdict on purpose (ADR-0004, ADR-0005). */
export interface ReportAccepted {
  visit_id: string;
  submitted_at: string;
}

// ---------------------------------------------------------------- dashboard (spec.md §6, ADR-0006)

export type AssignmentState = 'ASSIGNED' | 'EXPIRED' | 'FULFILLED';
export type VisitState = 'ACTIVE' | 'PENDING_REPORT' | 'COMPLETED' | 'ABANDONED' | 'UNREPORTED';

/**
 * The breakdown behind a verdict (ADR-0005): every fact the business may see,
 * and nothing about where the participant was. The snapshot and the
 * `COMPLETED` delta carry exactly this; the snapshot adds `computed_at`,
 * which on the delta is its `at`.
 */
export interface VerdictBreakdown {
  verdict: 'verified' | 'suspicious' | 'unverifiable';
  inside_s: number;
  outside_s: number;
  unattributed_s: number;
  attributed_total_s: number;
  dwell_ratio: number;
  conclusive_pings: number;
  total_pings: number;
  visit_duration_s: number;
  radius_m: number;
  min_duration_s: number;
  scoring_config_version: string;
}

export interface DashboardVerdict extends VerdictBreakdown {
  computed_at: string;
}

/** One attempt. `verdict` is `null` for every state but `COMPLETED`. */
export interface DashboardVisit {
  id: string;
  state: VisitState;
  started_at: string;
  ended_at: string | null;
  verdict: DashboardVerdict | null;
}

/** An assignment with every visit ever made against it, oldest first. */
export interface DashboardAssignment {
  id: string;
  business_name: string;
  participant_name: string;
  state: AssignmentState;
  deadline_at: string;
  radius_m: number;
  min_duration_s: number;
  visits: DashboardVisit[];
}

/** `GET /api/dashboard`, and the stream's first event (`snapshot`). */
export interface DashboardSnapshot {
  assignments: DashboardAssignment[];
}

/**
 * The stream's `visit` event. `from_state` is `null` for a visit that has just
 * started. `verdict` is present exactly when `to_state` is `COMPLETED`.
 */
export interface VisitDelta {
  visit_id: string;
  assignment_id: string;
  from_state: VisitState | null;
  to_state: VisitState;
  at: string;
  verdict: VerdictBreakdown | null;
}

/** The stream's `assignment` event: `EXPIRED` by the sweep, `FULFILLED` by a report. */
export interface AssignmentDelta {
  assignment_id: string;
  from_state: AssignmentState;
  to_state: AssignmentState;
  at: string;
}

/**
 * The body of every 4xx the API produces. Branch on `reason`, not on prose.
 * `illegal_transition` on a ping is the terminal one (spec.md §8).
 */
export interface ApiError {
  reason: 'not_found' | 'illegal_transition' | 'ping_too_old';
  message: string;
}

/**
 * The `ApiError` inside a failed request, or `null` if the failure was not one
 * the API produced (network down, proxy not running, a 5xx).
 *
 * Two shapes on the wire, one here. A 404 or 422 comes from FastAPI's
 * `HTTPException` and nests the body under `detail`; a 409 comes from the
 * `IllegalTransitionError` handler and puts it at the top level.
 */
export function apiError(err: unknown): ApiError | null {
  if (!(err instanceof HttpErrorResponse) || typeof err.error !== 'object' || err.error === null) {
    return null;
  }
  const body: unknown = 'detail' in err.error ? err.error.detail : err.error;
  if (typeof body === 'object' && body !== null && 'reason' in body && 'message' in body) {
    return body as ApiError;
  }
  return null;
}

/** A sentence for the participant, from whatever a request failed with. */
export function describeFailure(err: unknown): string {
  const known = apiError(err);
  if (known !== null) {
    return known.message;
  }
  if (err instanceof HttpErrorResponse) {
    return err.status === 0
      ? 'Could not reach the server. Check your connection.'
      : `The server answered ${err.status}.`;
  }
  return 'Something went wrong.';
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);
  private readonly base = inject(API_BASE_URL);

  /** GET /api/assignments/{id} → 200, or 404. */
  assignment(assignmentId: string): Observable<AssignmentDetails> {
    return this.http.get<AssignmentDetails>(`${this.base}/api/assignments/${assignmentId}`);
  }

  /** POST /api/assignments/{id}/visits → 201, or 409 if a visit is already open. */
  startVisit(assignmentId: string): Observable<VisitStarted> {
    return this.http.post<VisitStarted>(
      `${this.base}/api/assignments/${assignmentId}/visits`,
      null,
    );
  }

  /** POST /api/visits/{id}/pings → 202, 409 if not ACTIVE (terminal), 422 if stale. */
  sendPing(visitId: string, ping: PingRequest): Observable<PingAccepted> {
    return this.http.post<PingAccepted>(`${this.base}/api/visits/${visitId}/pings`, ping);
  }

  /** POST /api/visits/{id}/end → PENDING_REPORT. */
  endVisit(visitId: string): Observable<VisitEnded> {
    return this.http.post<VisitEnded>(`${this.base}/api/visits/${visitId}/end`, null);
  }

  /** POST /api/visits/{id}/report → COMPLETED. `body` must be non-empty. */
  submitReport(visitId: string, body: string): Observable<ReportAccepted> {
    return this.http.post<ReportAccepted>(`${this.base}/api/visits/${visitId}/report`, {
      body,
    });
  }

  /**
   * GET /api/dashboard/stream, as an `EventSource`: a `snapshot` event, then
   * `visit` and `assignment` deltas (ADR-0006). The caller owns it and must
   * `close()` it. Reconnection is the browser's default: on a dropped
   * connection it reopens and the server sends a fresh snapshot, so no
   * `Last-Event-ID` and no gap detection here, by design.
   */
  dashboardStream(): EventSource {
    return new EventSource(`${this.base}/api/dashboard/stream`);
  }
}
