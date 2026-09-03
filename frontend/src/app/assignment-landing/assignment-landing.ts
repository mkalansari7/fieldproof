import { Component, inject, signal } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { map } from 'rxjs';

/**
 * Screen 1 of the participant flow: the assignment landing (issue 07).
 *
 * Scaffolding only. The consent screen, the ping loop and the rest of the
 * flow are not here yet. The "Check location permission" button is tonight's
 * verification probe: it proves that `navigator.geolocation` prompts on a
 * page served over the dev server's self-signed HTTPS. Remove it once the
 * consent screen owns that call.
 */
@Component({
  selector: 'app-assignment-landing',
  templateUrl: './assignment-landing.html',
})
export class AssignmentLanding {
  private readonly route = inject(ActivatedRoute);

  readonly assignmentId = toSignal(
    this.route.paramMap.pipe(map((params) => params.get('assignmentId') ?? '')),
    { initialValue: '' },
  );

  readonly secureContext = window.isSecureContext;
  readonly locationProbe = signal('not asked yet');

  checkLocationPermission(): void {
    if (!('geolocation' in navigator)) {
      this.locationProbe.set('navigator.geolocation is undefined on this page');
      return;
    }
    this.locationProbe.set('asking…');
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const { latitude, longitude, accuracy } = position.coords;
        this.locationProbe.set(
          `granted: ${latitude.toFixed(5)}, ${longitude.toFixed(5)} (±${Math.round(accuracy)} m)`,
        );
      },
      (error) => {
        this.locationProbe.set(`error ${error.code}: ${error.message}`);
      },
      { enableHighAccuracy: true, timeout: 20_000, maximumAge: 0 },
    );
  }
}
