import { Routes } from '@angular/router';

import { Dashboard } from './dashboard/dashboard';
import { NotFound } from './not-found/not-found';
import { ParticipantFlow } from './participant-flow/participant-flow';

export const routes: Routes = [
  { path: 'a/:assignmentId', component: ParticipantFlow },
  { path: 'dashboard', component: Dashboard },
  { path: '**', component: NotFound },
];
