import { Routes } from '@angular/router';

import { NotFound } from './not-found/not-found';
import { ParticipantFlow } from './participant-flow/participant-flow';

export const routes: Routes = [
  { path: 'a/:assignmentId', component: ParticipantFlow },
  { path: '**', component: NotFound },
];
