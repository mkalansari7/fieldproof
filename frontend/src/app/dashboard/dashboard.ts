import { DatePipe, DecimalPipe, PercentPipe } from '@angular/common';
import { Component, DestroyRef, inject, signal } from '@angular/core';

import {
  ApiService,
  AssignmentDelta,
  AssignmentState,
  DashboardAssignment,
  DashboardSnapshot,
  DashboardVerdict,
  VisitDelta,
  VisitState,
} from '../api.service';

/**
 * One attempt as the table shows it: the wire's `DashboardVisit`, plus `at` —
 * when the visit entered the state it is in, as far as the dashboard knows.
 *
 * From the snapshot that is the verdict's `computed_at`, else `ended_at`, else
 * `started_at` (the sweeper stamps `ended_at` on `ABANDONED` too, to the last
 * ping). From a delta it is the delta's `at`. Either way it is the one time a
 * row carries besides `started_at`; there is no polyline, no last-seen counter
 * and no coordinate anywhere in this model (ADR-0005, ADR-0006).
 */
export interface VisitRow {
  id: string;
  state: VisitState;
  started_at: string;
  at: string;
  verdict: DashboardVerdict | null;
}

/** An assignment with its attempts. `at` is a delta's `at`; `null` until one arrives. */
export interface AssignmentRow {
  id: string;
  business_name: string;
  participant_name: string;
  state: AssignmentState;
  at: string | null;
  deadline_at: string;
  radius_m: number;
  min_duration_s: number;
  visits: VisitRow[];
}

/** What the page knows about its connection. Not a liveness counter; the socket's state. */
type Connection = 'connecting' | 'live' | 'reconnecting';

// ---------------------------------------------------------------- the model

/** The snapshot as rows. Called on every `snapshot` event, including after a reconnect. */
export function fromSnapshot(snapshot: DashboardSnapshot): AssignmentRow[] {
  return snapshot.assignments.map((assignment: DashboardAssignment) => ({
    ...assignment,
    at: null,
    visits: assignment.visits.map((visit) => ({
      id: visit.id,
      state: visit.state,
      started_at: visit.started_at,
      at: visit.verdict?.computed_at ?? visit.ended_at ?? visit.started_at,
      verdict: visit.verdict,
    })),
  }));
}

/**
 * Apply a `visit` delta. Idempotent: a delta carries the absolute `to_state`,
 * so the one that arrives both in the snapshot and as an event (the stream
 * subscribes before it snapshots; see `dashboard.py`) lands on a row already
 * in that state and changes nothing but `at`, to the same instant.
 *
 * A visit the table has not seen is appended: that is a start (`from_state`
 * `null`), and the attempt count grows by one. A `COMPLETED` delta carries
 * its breakdown, rendered by the same template as the snapshot's; the delta's
 * `at` stands in for `computed_at`.
 */
export function applyVisit(rows: AssignmentRow[], delta: VisitDelta): AssignmentRow[] {
  return rows.map((assignment) => {
    if (assignment.id !== delta.assignment_id) {
      return assignment;
    }
    const verdict: DashboardVerdict | null =
      delta.verdict === null ? null : { ...delta.verdict, computed_at: delta.at };
    const known = assignment.visits.some((visit) => visit.id === delta.visit_id);
    const visits = known
      ? assignment.visits.map((visit) =>
          visit.id === delta.visit_id
            ? { ...visit, state: delta.to_state, at: delta.at, verdict: verdict ?? visit.verdict }
            : visit,
        )
      : [
          ...assignment.visits,
          {
            id: delta.visit_id,
            state: delta.to_state,
            started_at: delta.at,
            at: delta.at,
            verdict,
          },
        ];
    return { ...assignment, visits };
  });
}

/** Apply an `assignment` delta. Idempotent for the same reason as `applyVisit`. */
export function applyAssignment(rows: AssignmentRow[], delta: AssignmentDelta): AssignmentRow[] {
  return rows.map((assignment) =>
    assignment.id === delta.assignment_id
      ? { ...assignment, state: delta.to_state, at: delta.at }
      : assignment,
  );
}

// ---------------------------------------------------------------- the page

/**
 * The business dashboard (issue 09): every assignment, every attempt against
 * it, and for a `COMPLETED` attempt the verdict with its breakdown.
 *
 * Snapshot then stream (ADR-0006). The `snapshot` event replaces the whole
 * table; `visit` and `assignment` events edit one row. One template renders
 * the result whichever way it arrived, which is what makes reconnection free:
 * the browser's `EventSource` reopens on its own, the server sends a fresh
 * snapshot, and the table is correct again with no replay and no gap check.
 *
 * What is not here, on purpose: no map, no trail, no coordinates (ADR-0005);
 * no "last seen Ns ago" counter and no per-ping events (ADR-0006). A
 * `suspicious` verdict is a prompt for a human to look, not a rejection, and
 * the assignment's `FULFILLED` sits beside it unmoved (ADR-0004).
 */
@Component({
  selector: 'app-dashboard',
  imports: [DatePipe, DecimalPipe, PercentPipe],
  templateUrl: './dashboard.html',
})
export class Dashboard {
  private readonly api = inject(ApiService);

  readonly assignments = signal<AssignmentRow[]>([]);
  readonly connection = signal<Connection>('connecting');
  /** `true` once a snapshot has arrived; before that an empty table means nothing. */
  readonly loaded = signal(false);

  private readonly stream: EventSource;

  constructor() {
    this.stream = this.api.dashboardStream();
    this.stream.addEventListener('snapshot', (event: MessageEvent<string>) => {
      this.assignments.set(fromSnapshot(JSON.parse(event.data) as DashboardSnapshot));
      this.loaded.set(true);
      this.connection.set('live');
    });
    this.stream.addEventListener('visit', (event: MessageEvent<string>) => {
      this.assignments.update((rows) => applyVisit(rows, JSON.parse(event.data) as VisitDelta));
    });
    this.stream.addEventListener('assignment', (event: MessageEvent<string>) => {
      this.assignments.update((rows) =>
        applyAssignment(rows, JSON.parse(event.data) as AssignmentDelta),
      );
    });
    // The browser reconnects by itself; the next snapshot flips this back.
    this.stream.onerror = () => this.connection.set('reconnecting');
    inject(DestroyRef).onDestroy(() => this.stream.close());
  }
}
