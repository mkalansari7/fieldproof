import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { map } from 'rxjs';

import { ApiService, AssignmentDetails, apiError, describeFailure } from '../api.service';

/** spec.md §1 `PING_INTERVAL_S`, in the unit `setInterval` takes. */
const PING_INTERVAL_MS = 15_000;

/** spec.md §1 `ABANDON_AFTER_S`, as the consent screen states it. */
const ABANDON_AFTER_MIN = 15;

/** Fresh fix each time (`maximumAge: 0`): a cached one would be `ping_too_old` (spec.md §4). */
const POSITION_OPTIONS: PositionOptions = {
  enableHighAccuracy: true,
  timeout: 20_000,
  maximumAge: 0,
};

/** The screens, in the order a visit walks them (issue 07). `closed` is the 409 exit. */
type Screen =
  'loading' | 'unavailable' | 'landing' | 'consent' | 'active' | 'closed' | 'report' | 'done';

/**
 * What the consent screen learned by asking for one position. Asked *before*
 * Start so that a participant whose location is denied is told the visit will
 * be unverifiable before they begin, not after (spec.md §8).
 */
type LocationCheck =
  | { kind: 'unchecked' }
  | { kind: 'checking' }
  | { kind: 'granted'; accuracy_m: number }
  | { kind: 'denied' }
  | { kind: 'failed'; message: string };

/**
 * The participant flow: landing → consent → active → end → report → done.
 *
 * One component, because one visit's state — the id, the interval, the Wake
 * Lock — has to survive every screen change, and the 409 handler has to reach
 * all three from a ping callback. The verdict never appears here (ADR-0005):
 * the flow ends at "done".
 *
 * `setInterval` + `getCurrentPosition`, not `watchPosition`: the latter fires
 * on movement, and a participant standing still in a shop may produce almost
 * nothing (spec.md §8).
 */
@Component({
  selector: 'app-participant-flow',
  imports: [DatePipe, DecimalPipe],
  templateUrl: './participant-flow.html',
})
export class ParticipantFlow {
  private readonly api = inject(ApiService);
  private readonly route = inject(ActivatedRoute);

  readonly assignmentId = toSignal(
    this.route.paramMap.pipe(map((params) => params.get('assignmentId') ?? '')),
    { initialValue: '' },
  );

  readonly screen = signal<Screen>('loading');
  readonly assignment = signal<AssignmentDetails | null>(null);
  readonly loadError = signal('');
  readonly minDurationMin = computed(() =>
    Math.round((this.assignment()?.min_duration_s ?? 0) / 60),
  );

  readonly locationCheck = signal<LocationCheck>({ kind: 'unchecked' });
  readonly startError = signal('');

  readonly visitId = signal('');
  readonly pingsSent = signal(0);
  readonly lastPingAt = signal<Date | null>(null);
  readonly wakeLockHeld = signal(false);
  /** Shown, never swallowed: a participant with a broken fix must know. */
  readonly locationError = signal('');
  readonly pingError = signal('');
  readonly endError = signal('');

  readonly reportDeadlineAt = signal('');
  readonly reportBody = signal('');
  readonly reportError = signal('');

  /** Disables the button whose request is in flight. */
  readonly busy = signal(false);

  readonly abandonAfterMin = ABANDON_AFTER_MIN;
  readonly pingIntervalS = PING_INTERVAL_MS / 1000;

  private interval: ReturnType<typeof setInterval> | null = null;
  private wakeLock: WakeLockSentinel | null = null;
  private readonly onVisibilityChange = (): void => {
    // The browser drops the lock when the tab is hidden; take it back on return.
    if (document.visibilityState === 'visible' && this.interval !== null) {
      void this.requestWakeLock();
    }
  };

  constructor() {
    inject(DestroyRef).onDestroy(() => this.stopTracking());
    this.load('landing');
  }

  // ---------------------------------------------------------------- landing

  private load(then: 'landing' | 'consent'): void {
    this.screen.set('loading');
    this.api.assignment(this.assignmentId()).subscribe({
      next: (assignment) => {
        this.assignment.set(assignment);
        this.screen.set(assignment.state === 'ASSIGNED' ? then : 'landing');
      },
      error: (err: unknown) => {
        this.loadError.set(
          apiError(err)?.reason === 'not_found'
            ? 'This link does not match any assignment. Check the link you were sent.'
            : describeFailure(err),
        );
        this.screen.set('unavailable');
      },
    });
  }

  toConsent(): void {
    this.screen.set('consent');
  }

  // ---------------------------------------------------------------- consent

  checkLocation(): void {
    if (!('geolocation' in navigator)) {
      this.locationCheck.set({ kind: 'failed', message: 'This browser has no geolocation.' });
      return;
    }
    this.locationCheck.set({ kind: 'checking' });
    navigator.geolocation.getCurrentPosition(
      (position) => {
        this.locationCheck.set({ kind: 'granted', accuracy_m: position.coords.accuracy });
      },
      (error) => {
        this.locationCheck.set(
          error.code === error.PERMISSION_DENIED
            ? { kind: 'denied' }
            : { kind: 'failed', message: error.message },
        );
      },
      POSITION_OPTIONS,
    );
  }

  start(): void {
    this.busy.set(true);
    this.startError.set('');
    this.api.startVisit(this.assignmentId()).subscribe({
      next: (started) => {
        this.busy.set(false);
        this.visitId.set(started.visit_id);
        this.pingsSent.set(0);
        this.lastPingAt.set(null);
        this.locationError.set('');
        this.pingError.set('');
        this.endError.set('');
        this.screen.set('active');
        this.startTracking();
      },
      error: (err: unknown) => {
        this.busy.set(false);
        this.startError.set(describeFailure(err));
      },
    });
  }

  // ---------------------------------------------------------------- active

  private startTracking(): void {
    void this.requestWakeLock();
    document.addEventListener('visibilitychange', this.onVisibilityChange);
    this.tick();
    this.interval = setInterval(() => this.tick(), PING_INTERVAL_MS);
  }

  private stopTracking(): void {
    if (this.interval !== null) {
      clearInterval(this.interval);
      this.interval = null;
    }
    document.removeEventListener('visibilitychange', this.onVisibilityChange);
    void this.releaseWakeLock();
  }

  private tick(): void {
    navigator.geolocation.getCurrentPosition(
      (position) => {
        this.locationError.set('');
        this.sendPing(position);
      },
      (error) => {
        this.locationError.set(`Location failed: ${error.message} (code ${error.code}).`);
      },
      POSITION_OPTIONS,
    );
  }

  private sendPing(position: GeolocationPosition): void {
    const visitId = this.visitId();
    this.api
      .sendPing(visitId, {
        lat: position.coords.latitude,
        lng: position.coords.longitude,
        accuracy_m: position.coords.accuracy,
        reported_at: new Date(position.timestamp).toISOString(),
      })
      .subscribe({
        next: () => {
          if (this.interval === null) {
            return; // A ping that landed after End; the trail is sealed.
          }
          this.pingsSent.update((n) => n + 1);
          this.lastPingAt.set(new Date());
          this.pingError.set('');
        },
        error: (err: unknown) => {
          // spec.md §8: 409 on a ping is terminal. 422 (`ping_too_old`) is not —
          // one reading is dropped and the interval keeps running.
          if (apiError(err)?.reason === 'illegal_transition') {
            this.closeVisit();
            return;
          }
          this.pingError.set(`Location not recorded: ${describeFailure(err)}`);
        },
      });
  }

  /** The terminal 409 (spec.md §8): stop, release, tell them, offer a new visit. */
  private closeVisit(): void {
    this.stopTracking();
    this.screen.set('closed');
  }

  private async requestWakeLock(): Promise<void> {
    if (!('wakeLock' in navigator)) {
      return;
    }
    try {
      this.wakeLock = await navigator.wakeLock.request('screen');
      this.wakeLockHeld.set(true);
      this.wakeLock.addEventListener('release', () => this.wakeLockHeld.set(false));
    } catch {
      // Best-effort (spec.md §8): low battery, an unsupported browser, a
      // hidden tab. The indicator shows the lock is not held.
      this.wakeLockHeld.set(false);
    }
  }

  private async releaseWakeLock(): Promise<void> {
    const lock = this.wakeLock;
    this.wakeLock = null;
    if (lock !== null) {
      try {
        await lock.release();
      } catch {
        // Already released by the browser; nothing to do.
      }
    }
    this.wakeLockHeld.set(false);
  }

  end(): void {
    this.busy.set(true);
    this.endError.set('');
    this.api.endVisit(this.visitId()).subscribe({
      next: (ended) => {
        this.busy.set(false);
        this.stopTracking();
        this.reportDeadlineAt.set(ended.report_deadline_at);
        this.reportBody.set('');
        this.reportError.set('');
        this.screen.set('report');
      },
      error: (err: unknown) => {
        this.busy.set(false);
        // Not ACTIVE any more means the sweeper closed it under us: the same
        // exit as a 409 on a ping, and the same message.
        if (apiError(err)?.reason === 'illegal_transition') {
          this.closeVisit();
          return;
        }
        this.endError.set(describeFailure(err));
      },
    });
  }

  // ---------------------------------------------------------------- closed

  startAgain(): void {
    this.visitId.set('');
    this.startError.set('');
    this.load('consent');
  }

  // ---------------------------------------------------------------- report

  submitReport(): void {
    const body = this.reportBody().trim();
    if (body === '') {
      this.reportError.set('Write something before submitting.');
      return;
    }
    this.busy.set(true);
    this.reportError.set('');
    this.api.submitReport(this.visitId(), body).subscribe({
      next: () => {
        this.busy.set(false);
        this.screen.set('done');
      },
      error: (err: unknown) => {
        this.busy.set(false);
        this.reportError.set(describeFailure(err));
      },
    });
  }
}
