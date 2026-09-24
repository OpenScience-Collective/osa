/**
 * A widget_e2e.py server on a free port, for the Chrome checks that start their own
 * (color-scheme-check.mjs and first-paint-check.mjs, each with --serve). Its output
 * is kept, so a server that fails to start can be reported with what it said.
 */

const REPO_ROOT = new URL('../..', import.meta.url).pathname;

export function startServer(extraArgs) {
  const probe = Bun.listen({ hostname: '127.0.0.1', port: 0, socket: { data() {} } });
  const port = probe.port;
  probe.stop(true);
  const proc = Bun.spawn(['uv', 'run', 'python', 'frontend/browser-harness/widget_e2e.py', String(port), ...extraArgs], {
    cwd: REPO_ROOT, stdout: 'pipe', stderr: 'pipe',
  });
  const server = { port, proc, output: '' };
  for (const stream of [proc.stdout, proc.stderr]) {
    (async () => {
      const decoder = new TextDecoder();
      for await (const chunk of stream) server.output += decoder.decode(chunk);
    })();
  }
  return server;
}

// True once the server answers; false if it exits or the deadline passes, when the
// caller reports server.output.
export async function waitForServer(server, timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (server.proc.exitCode !== null) break;
    try {
      const response = await fetch(`http://127.0.0.1:${server.port}/browser-harness/widget-e2e-config.js`);
      if (response.ok) return true;
    } catch {
      // not listening yet
    }
    await Bun.sleep(250);
  }
  return false;
}
