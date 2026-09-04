import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideHttpClient, withFetch } from '@angular/common/http';
import { provideRouter } from '@angular/router';

import { routes } from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes),
    provideHttpClient(withFetch()),
    // The API is reached at /api on this page's origin, through the dev
    // server's proxy (proxy.conf.json). To call it directly instead:
    // { provide: API_BASE_URL, useValue: 'https://api.example:8000' },
  ],
};
