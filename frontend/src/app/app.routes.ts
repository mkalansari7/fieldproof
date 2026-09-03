import { Routes } from '@angular/router';

import { AssignmentLanding } from './assignment-landing/assignment-landing';
import { NotFound } from './not-found/not-found';

export const routes: Routes = [
  { path: 'a/:assignmentId', component: AssignmentLanding },
  { path: '**', component: NotFound },
];
