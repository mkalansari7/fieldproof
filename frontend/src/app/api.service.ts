import { HttpClient } from '@angular/common/http';
import { Injectable, InjectionToken, inject } from '@angular/core';
import { Observable } from 'rxjs';

/**
 * Where the fieldproof API lives, without a trailing slash.
 *
 * Defaults to port 8000 on whatever host served this page, so a phone that
 * reached the dev server at https://192.168.1.88:4200 talks to the API at
 * http://192.168.1.88:8000 with no configuration. Override by providing this
 * token in `app.config.ts`.
 */
export const API_BASE_URL = new InjectionToken<string>('API_BASE_URL', {
  providedIn: 'root',
  factory: () => `http://${window.location.hostname}:8000`,
});

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

/**
 * The body of every 4xx the API produces. Branch on `reason`, not on prose.
 * `illegal_transition` on a ping is the terminal one (spec.md §8).
 */
export interface ApiError {
  reason: 'not_found' | 'illegal_transition' | 'ping_too_old';
  message: string;
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);
  private readonly base = inject(API_BASE_URL);

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
}
