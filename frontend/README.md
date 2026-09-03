# fieldproof frontend

The participant flow (issue 07). Angular 21, standalone components, zoneless,
no component library: unstyled semantic HTML on purpose.

## Run

```bash
npm install
npm start          # ng serve, with ssl + host 0.0.0.0 from angular.json
```

The dev server listens on `https://0.0.0.0:4200` with a self-signed
certificate that Angular generates on each start. HTTPS is not optional: iOS
refuses `navigator.geolocation` to a non-secure origin (ai-log, 2026-09-03).

Open the participant landing at `https://<mac-lan-ip>:4200/a/<assignment id>`.
The three seeded assignment ids are in `src/fieldproof/seed.py`.

## Node

Angular is pinned to 21.x because the machine's default Node is 20.19, which
the newer CLI refuses. `engines` in `package.json` records the accepted range.
Upgrading Angular means upgrading Node first.

## API base URL

`ApiService` calls `http://<hostname of this page>:8000` by default, so a phone
that loaded the page from the Mac's LAN address talks to the API on the same
address. Provide `API_BASE_URL` in `app.config.ts` to point elsewhere.

## Layout

- `src/app/api.service.ts`: start / ping / end / report, typed to mirror the
  response models in `src/fieldproof/api.py`.
- `src/app/app.routes.ts`: `/a/:assignmentId` is the only real route.
- `src/app/assignment-landing/`: screen 1. Still scaffolding.
