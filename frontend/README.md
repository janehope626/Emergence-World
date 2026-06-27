# Emergence World Observatory

Read-only React observability client for the FastAPI trace service.

```bash
# terminal 1, repository root
source .venv/bin/activate
world demo-trace
world serve --cors-origins http://127.0.0.1:5173

# terminal 2
cd frontend
npm install
npm run dev
```

Vite proxies `/api` and `/ws` to `127.0.0.1:8000` in development. Override the
deployment endpoints with `VITE_API_BASE` and `VITE_WS_URL`.

```bash
npm run build
npm test
npx playwright install chromium
npm run test:e2e
```

The WebSocket client persists the last committed `stream_sequence`. Provisional
events are shown as live activity but do not advance that cursor. A `stream.gap` or
`command.committed` event triggers REST reconciliation.
