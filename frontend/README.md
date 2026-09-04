# fieldproof frontend

The participant flow (issue 07) and the business dashboard (issue 09). Angular
21, standalone components, zoneless, no component library: semantic HTML and
one small stylesheet.

## Run

```bash
npm install
npm start          # ng serve, with ssl + host 0.0.0.0 from angular.json
```

The dev server listens on `https://0.0.0.0:4200` with a self-signed
certificate that Angular generates on each start. HTTPS is not optional: iOS
refuses `navigator.geolocation` to a non-secure origin (ai-log, 2026-09-03).

Open the participant landing at `https://<mac-lan-ip>:4200/a/<assignment id>`.
The three seeded assignment ids are in `src/fieldproof/seed.py`. The business
dashboard is at `https://localhost:4200/dashboard`.

## Node

Angular is pinned to 21.x because the machine's default Node is 20.19, which
the newer CLI refuses. `engines` in `package.json` records the accepted range.
Upgrading Angular means upgrading Node first.

## API base URL

The page calls `/api/...` on its own origin, and the dev server proxies that
to `http://localhost:8000` (`proxy.conf.mjs`, wired in `angular.json`). So a
phone that loaded the page over the self-signed HTTPS talks to the API through
the same connection, and the browser never sees a plain-HTTP request from an
HTTPS page, which it would block as mixed content. Run the API on the Mac
before `npm start`. To call an API elsewhere, provide `API_BASE_URL` in
`app.config.ts`.

The proxy config is JavaScript, not JSON, for one hook: it ends the page's
response when the API's response closes. Without it the dev server's proxy
leaves `/api/dashboard/stream` open on a dead upstream, the browser's
`EventSource` never reconnects, and the dashboard silently goes stale across
an API restart. Measured with `curl -N` through the proxy; see the file.

## Layout

- `src/app/api.service.ts`: assignment / start / ping / end / report, typed
  to mirror the response models in `src/fieldproof/api.py`, plus `apiError`
  for reading the `{reason, message}` body off a failed request, and
  `dashboardStream`, the `EventSource` on `/api/dashboard/stream`.
- `src/app/app.routes.ts`: `/a/:assignmentId` and `/dashboard`.
- `src/app/participant-flow/`: the whole participant flow in one component,
  landing → consent → active → closed | report → done (spec.md §8). The
  interval, the Wake Lock and the terminal 409 handler live here.
- `src/app/dashboard/`: the business dashboard, one component. The `snapshot`
  event replaces the table, `visit` and `assignment` deltas edit one row, and
  one template renders both (ADR-0006). A `COMPLETED` row shows the verdict
  and its breakdown; nothing shows a trail or a coordinate (ADR-0005).
  Reconnection is the browser's default `EventSource` behaviour: the server's
  fresh snapshot makes it correct.
- `src/styles.css`: the one stylesheet.

## Not tested

No component tests, per the test plan: a broken screen is loud in the demo.
The API endpoints the flow calls are tested in `tests/test_api.py`.
