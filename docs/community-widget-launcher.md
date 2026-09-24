## Launcher: the capsule, the notebook icon, and the HPC placeholder

The widget's floating launcher has two shapes: `bubble`, today's single chat button,
and `capsule`, a vertical stack of three circular icons (issue #436).
Collapsed, a `capsule` widget looks identical to a `bubble` one: one chat button, bottom right.
Clicking it opens the chat panel and expands the capsule upward,
revealing a notebook icon and an HPC placeholder above the chat button;
the chat button itself never moves.
A community that never sets `launcher` renders exactly as it did before this field existed,
markup and computed styles included; that equivalence is asserted in
`frontend/test-widget-capsule.js`.

### `launcher` and `launcher_label`

```yaml
widget:
  launcher: capsule
  launcher_label: "Explore NEMAR"
```

Both fields live on `WidgetConfig` (`src/core/config/community.py`)
and reach the frontend through `WidgetConfigResponse` (`src/api/routers/community.py`),
the same path `theme_color` and `user_bubble_color` already use.
`resolve()` omits `launcher` for the `bubble` default and omits `launcher_label` when unset,
so an older or unconfigured community sends neither key and the widget's own defaults apply.

- `launcher`: `"bubble"` (default) or `"capsule"`.
- `launcher_label`: plain text, at most 40 characters, stripped, no `<`/`>` markup.
  Replaces the hardcoded collapsed-launcher tooltip,
  `Ask me about <title>`, with a shorter label of the community's choosing.
  Unset keeps that exact hardcoded text.
  The greeting (`initial_message`), suggested questions and the rest of the panel
  stay behind the click either way; this field only changes what shows *before* the click.

The widget loads both from the community config endpoint the same way it loads every other
widget key, respecting an embedder's own `setConfig` call
(`OSAChatWidget.setConfig({ launcher: 'bubble' })` before `init()` overrides the community
config, exactly as `themeColor` and the other color keys already do).

### The three icons

Bottom to top: chat (today's button, unchanged), notebook, HPC.
Each is an accessible button with an `aria-label` naming its current state
and a hover/keyboard-focus tooltip in the same look as the collapsed launcher's own tooltip.
An icon that cannot be used right now carries `aria-disabled="true"`,
never the `disabled` attribute,
so it stays focusable and a screen reader can still reach its reason.

The HPC icon is a placeholder everywhere: always `aria-disabled`,
always tooltipped "HPC submission is coming soon", and always carrying a small "Soon" badge.
Submission to a cluster sits outside OSA (`nemarOrg/nemar-cli` ADR 0049)
and is drawn now only so the capsule does not need redesigning once it exists.

The notebook icon reflects the dataset on screen, from `setDataset` (below), in four states:

| State | `aria-disabled` | Tooltip |
|---|---|---|
| No dataset (never set, or explicitly `null`) | `true` | Open a dataset page to start a notebook |
| A dataset, Zarr copy unknown | `true` | Checking whether this dataset has a Zarr copy |
| A dataset with no Zarr copy (`zarr: false`) | `true` | This dataset has no Zarr copy yet, so there is nothing to open in a notebook |
| A dataset with a Zarr copy (`zarr: true`) | `false` | Open `<id>` in a Python notebook (JupyterLite, opens a new tab) |

"Inactive" (no dataset, or no Zarr copy) and "coming soon" (HPC) read differently on
purpose: an inactive icon is muted (a neutral surface, a border, a dimmer icon color);
"coming soon" carries the badge on top of that same muted look.
Neither is just a lower opacity on the active look, which would read as broken rather than deliberate.

### `OSAChatWidget.setDataset(value)`

The embedding page tells the widget which dataset, if any, is on screen:

```js
OSAChatWidget.setDataset({ id: 'nm000103', zarr: true });
OSAChatWidget.setDataset(null); // not a dataset page
```

- `value` is `null` (not a dataset page) or an object `{ id, zarr }`.
- `id` must match `^[A-Za-z0-9._-]{1,64}$`.
- `zarr` is `true` (a Zarr copy exists), `false` (it does not), or absent/`undefined`
  (not known yet, e.g. the check is still in flight).
- Invalid input (a malformed `id`, or a `zarr` that is not `true`/`false`/absent)
  is ignored with a `console.warn`; it never throws, and it never changes the
  previously-set state.

`setDataset` can be called before `OSAChatWidget.init()` runs,
since the embedder's own dataset-detection script may load before or after the widget script.
The value is stored either way and rendered once the capsule exists;
every later call re-renders immediately.

Clicking the active notebook icon opens:

```
${notebookUrl}open.html?community=${encodeURIComponent(communityId)}&dataset=${encodeURIComponent(id)}
```

in a new tab (`window.open(url, '_blank', 'noopener')`).
`communityId` is the widget's own configured community; `notebookUrl` is below.
That URL is the notebook site's contract: it validates both parameters,
writes a starter notebook into JupyterLite's own storage, and redirects into it.

### `OSAChatWidget.setConfig({ notebookUrl })`

```js
OSAChatWidget.setConfig({ notebookUrl: 'https://notebook.osc.earth/osa/' });
```

The notebook site's base URL, set by the embedder the same way `apiEndpoint` is,
since staging and production serve it from different hosts.
Defaults to `https://notebook.osc.earth/osa/`; `/osa/` names this widget's own project
on the shared `notebook.osc.earth` plane, per OSC's subdomain-per-plane,
path-per-project naming rule, so the path is expected and preserved, not stripped.

Accepted values: an absolute `https:` URL, or an `http:` URL on `localhost`/`127.0.0.1`
(for local testing against a dev server), with or without a path. Anything else is
ignored with a `console.warn`, and the previous value (the default, or whatever was
set before) is kept. An accepted value is normalized to end in a trailing slash
before it is stored.

### Testing

`frontend/test-widget-capsule.js` runs the real widget source in a happy-dom window
(the same technique `frontend/test-widget-tools.js` uses) and covers: the capsule
existing only under `launcher: capsule`; the bubble-mode markup and className staying
byte-for-byte unchanged; all four notebook states; `setDataset` before and after
`init()`; invalid `setDataset` and `notebookUrl` inputs being ignored; the exact,
encoded notebook URL a click opens; an inactive button's click opening nothing; and
`launcher_label` overriding the collapsed tooltip.

`frontend/browser-harness/widget_e2e.py --nemar` serves NEMAR's real, capsule-enabled
config for a manual or scripted Chrome check; `widget-e2e-dataset.js`, loaded by
`widget-e2e.html`, reads `?dataset=<id>&zarr=true|false&notebookUrl=<url>` from the page's
own URL and calls `setDataset`/`setConfig` accordingly, so a run does not need a devtools
console to exercise a given notebook state.
