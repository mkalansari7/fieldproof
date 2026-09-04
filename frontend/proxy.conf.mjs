// The dev server's /api proxy (README "API base URL"). JavaScript rather than
// JSON for one reason: the `configure` hook below.
//
// The dev server's proxy pipes the API's response into the page's and never
// ends the page's side when the API's side closes mid-stream. For an ordinary
// request that is invisible; for `/api/dashboard/stream` it means an API
// restart leaves the dashboard's EventSource open on a dead upstream, so the
// browser never reconnects and the table silently stops updating. Ending the
// page's response when the upstream response closes is what a real reverse
// proxy does anyway, and it is what lets ADR-0006's "reconnect and re-snapshot"
// hold in development too. Measured: without this, `curl -N` through the
// proxy outlives the API by as long as you care to wait; with it, both sides
// close within the API's graceful-shutdown timeout.
export default {
  '/api': {
    target: 'http://localhost:8000',
    secure: false,
    configure(proxy) {
      proxy.on('proxyRes', (proxyRes, _req, res) => {
        proxyRes.on('close', () => {
          if (!res.writableEnded) {
            res.end();
          }
        });
      });
    },
  },
};
