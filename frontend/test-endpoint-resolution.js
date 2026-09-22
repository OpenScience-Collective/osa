/**
 * Tests for OSA Widget API Endpoint Resolution (#437)
 *
 * The widget must call the Cloudflare Worker's stable *.osc.earth custom
 * domain, never the account-scoped *.workers.dev hostname (that hostname
 * gets pinned into consumers' Content-Security-Policy, e.g. nemarOrg/website
 * ADR 0017, and moving accounts should not require every consumer to change
 * too). This loads the real widget source in a vm context, varies
 * window.location.hostname across every environment class the widget
 * detects, and reads back the resolved endpoint via the same
 * OSAChatWidget.getConfig().apiEndpoint that frontend/index.html now reuses
 * instead of re-deriving the hostname logic itself.
 *
 * Run with: bun frontend/test-endpoint-resolution.js
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

let testsPassed = 0;
let testsFailed = 0;

function assert(condition, message) {
  if (!condition) {
    console.error(`  x FAIL: ${message}`);
    testsFailed++;
  } else {
    console.log(`  ok ${message}`);
    testsPassed++;
  }
}

function assertEqual(actual, expected, message) {
  if (actual !== expected) {
    console.error(`  x FAIL: ${message}`);
    console.error(`    Expected: ${expected}`);
    console.error(`    Actual:   ${actual}`);
    testsFailed++;
  } else {
    console.log(`  ok ${message}`);
    testsPassed++;
  }
}

const WIDGET_PATH = path.join(__dirname, 'osa-chat-widget.js');
const widgetSource = fs.readFileSync(WIDGET_PATH, 'utf8');

const PROD_ENDPOINT = 'https://widget.osc.earth/osa';
const DEV_ENDPOINT = 'https://develop-widget.osc.earth/osa';

// Load the widget fresh for a given hostname and return its resolved
// OSAChatWidget public API, the same way test-streaming.js's "Production
// done reducer" test loads it: a minimal window/document stub, no DOM.
function loadWidgetForHostname(hostname) {
  const window = {
    location: { hostname, origin: `https://${hostname}`, href: `https://${hostname}/` },
  };
  const context = {
    console,
    document: {
      currentScript: { hasAttribute: () => true },
      readyState: 'loading',
    },
    window,
  };
  vm.runInNewContext(widgetSource, context);
  return window.OSAChatWidget;
}

function resolvedEndpoint(hostname) {
  return loadWidgetForHostname(hostname).getConfig().apiEndpoint;
}

console.log('='.repeat(60));
console.log('OSA Widget Endpoint Resolution Tests');
console.log('='.repeat(60));

console.log('\nProduction hostnames resolve to the production custom domain');
assertEqual(resolvedEndpoint('demo.osc.earth'), PROD_ENDPOINT, 'demo.osc.earth -> production endpoint');
assertEqual(resolvedEndpoint('osa-demo.pages.dev'), PROD_ENDPOINT, 'osa-demo.pages.dev -> production endpoint');

console.log('\nDev hostnames resolve to the dev custom domain');
assertEqual(resolvedEndpoint('develop-demo.osc.earth'), DEV_ENDPOINT, 'develop-demo.osc.earth -> dev endpoint');
assertEqual(
  resolvedEndpoint('feature-123-demo.osc.earth'),
  DEV_ENDPOINT,
  'feature-123-demo.osc.earth (a *-demo.osc.earth preview) -> dev endpoint'
);
assertEqual(
  resolvedEndpoint('feature-branch.osa-demo.pages.dev'),
  DEV_ENDPOINT,
  'feature-branch.osa-demo.pages.dev (a *.osa-demo.pages.dev preview) -> dev endpoint'
);
assertEqual(resolvedEndpoint('localhost'), DEV_ENDPOINT, 'localhost -> dev endpoint');
assertEqual(resolvedEndpoint('127.0.0.1'), DEV_ENDPOINT, '127.0.0.1 -> dev endpoint');

console.log('\nAn unrecognized hostname falls through to the production default');
assertEqual(
  resolvedEndpoint('example.com'),
  PROD_ENDPOINT,
  'an unrecognized hostname is treated as production (existing, intentional default)'
);

console.log('\nNo trace of the retired account-scoped workers.dev hostname remains');
assert(
  !widgetSource.includes('workers.dev'),
  'widget source contains no "workers.dev" string'
);
assert(
  !widgetSource.includes('shirazi'),
  'widget source contains no reference to the retired account subdomain'
);
assert(
  !widgetSource.includes('assistant.osc.earth'),
  'widget source contains no reference to the retired assistant.osc.earth name'
);

console.log('\nThe resolved endpoints are the documented *.osc.earth custom domains');
assert(widgetSource.includes(`'${PROD_ENDPOINT}'`), `widget source contains the literal ${PROD_ENDPOINT}`);
assert(widgetSource.includes(`'${DEV_ENDPOINT}'`), `widget source contains the literal ${DEV_ENDPOINT}`);

// Print summary
console.log('\n' + '='.repeat(60));
console.log('Test Summary');
console.log('='.repeat(60));
console.log(`Total: ${testsPassed + testsFailed} tests`);
console.log(`Passed: ${testsPassed}`);
console.log(`Failed: ${testsFailed}`);

if (testsFailed === 0) {
  console.log('\nAll tests passed!');
  process.exit(0);
} else {
  console.log(`\n${testsFailed} test(s) failed`);
  process.exit(1);
}
