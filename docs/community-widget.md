# The community widget

Every community's `config.yaml` has an optional `widget:` block
(`WidgetConfig` in `src/core/config/community.py`) that controls how its chat
widget looks and behaves once embedded on a page.
All fields are optional: a community that sets none of them gets the
platform's own defaults, unchanged.

This page documents that block, field by field, one section per area so a
later area (for example, a launcher-button area) can be added as its own
section without disturbing the others.
For the runtime a community's model can execute code in, see
[`docs/community-browser-runtime.md`](community-browser-runtime.md) instead;
this page is about the widget's appearance and copy.

## Text and behavior

| Field | Type | Default | What it changes |
|---|---|---|---|
| `title` | string, up to 100 characters | the community's own `name` | The widget header's title text. |
| `initial_message` | string, up to 1000 characters | none | The first greeting message shown when the widget opens. |
| `placeholder` | string, up to 200 characters | `"Ask a question..."` | The chat input field's placeholder text. |
| `suggested_questions` | list of strings, at most 10 | empty list | Clickable suggestion buttons shown below the initial message. Empty and whitespace-only entries are dropped. |
| `logo_url` | string: an `http://`/`https://` URL, or a path starting with `/` | none | A custom logo/icon for the widget header avatar. When unset, the API looks for a `logo.*` file (SVG, PNG, JPG, JPEG, WEBP) in the community's own folder; failing that, the widget falls back to a default brain icon. |

```yaml
widget:
  title: My Tool Assistant
  initial_message: "Hi! I can help you find datasets and answer questions about My Tool."
  placeholder: Ask about My Tool...
  suggested_questions:
    - What formats does My Tool support?
    - How do I cite My Tool?
  logo_url: /static/my-tool-logo.svg
```

## Color roles

A widget has four places a community's own color can appear, and each one
has its own field, because the SAME hex code is very rarely right for all
four at once:

- **Surface** (`theme_color`): the color PAINTED as a background. The
  launcher button, the header, the Send button, and every other button that
  otherwise reads the platform blue.
- **Text on that surface** (`theme_text_color`): the color drawn ON the
  surface above, for its own text and icons. Defaults to white, which is why
  a community with a light `theme_color` (any color whose contrast against
  white text falls under 4.5:1) needs to set this explicitly, or its own
  header text becomes unreadable.
- **The same hue used as a foreground on white** (`accent_color`): links,
  borders, focus rings and native checkbox `accent-color` on the widget's
  white panel, where `theme_color` is read AS TEXT rather than painted as a
  background. Defaults to `theme_color` itself, which is correct whenever
  that color is already dark enough to read on white; a light `theme_color`
  needs a separate, darker `accent_color` for this role, exactly as it needs
  a separate `theme_text_color` for the surface role.
- **The reader's own message bubble** (`user_bubble_color` and
  `user_bubble_text_color`): a second surface/text pair, independent of the
  first, for the bubbles the reader's own messages render in. Defaults to
  the platform blue with white text; setting `user_bubble_color` alone keeps
  white text, exactly like `theme_color` alone keeps white header text.

| Field | Type | Default | Role |
|---|---|---|---|
| `theme_color` | hex `#RRGGBB` | platform blue `#2563eb` | Surface: launcher, header, Send, Run, and similar buttons. |
| `theme_text_color` | hex `#RRGGBB` | white `#ffffff` | Text and icons on `theme_color` surfaces. |
| `accent_color` | hex `#RRGGBB` | `theme_color` itself | `theme_color`'s hue used as a foreground on the white panel: links, borders, focus rings, checkbox `accent-color`. |
| `user_bubble_color` | hex `#RRGGBB` | platform blue `#2563eb` | Surface: the reader's own message bubbles. |
| `user_bubble_text_color` | hex `#RRGGBB` | white `#ffffff` | Text in the reader's own message bubbles. |

### Contrast guidance

Every text-on-surface pair (`theme_text_color` on `theme_color`,
`user_bubble_text_color` on `user_bubble_color`, and `accent_color` on the
widget's white panel) needs at least a 4.5:1 WCAG 2 contrast ratio, the same
bar `tests/test_assistants/test_nemar_runtime.py::TestTheWidgetColors`
enforces for NEMAR's own colors.
Nothing in `WidgetConfig` checks this for a new community, so measure it
before publishing: a color picker's own contrast checker, or the WCAG
formula directly (relative luminance from sRGB, then
`(lighter + 0.05) / (darker + 0.05)`).

Also check `theme_text_color` against the DERIVED hover shade the widget
computes from `theme_color` (`applyWidgetConfig()` in
`frontend/osa-chat-widget.js` subtracts 25 from each RGB channel, floored at
0), since a hover state that reads fine on the surface itself can still fail
against a darker hover.

### NEMAR's own widget, as an example

NEMAR's `theme_color` is nemar.org's own brand teal (`#5bbad5`), the exact
color of its home page search button, which pairs a dark `theme_text_color`
(`#04121f`, that button's own text color) rather than white, because white
text on `#5bbad5` is only 2.2:1.
The same teal is too light to read as a foreground on the white panel
(also 2.2:1), so `accent_color` names a separate, darker step of the same
193-degree hue (`#257a92`, 4.9:1 on white) for that role instead.

```yaml
widget:
  theme_color: "#5bbad5"          # nemar.org's --brand-teal
  theme_text_color: "#04121f"     # nemar.org's search button's own text color
  user_bubble_color: "#5bbad5"
  user_bubble_text_color: "#04121f"
  accent_color: "#257a92"         # the same hue, darkened for foreground use on white
```

## Launcher: the capsule, the notebook icon, and the high-performance computing (HPC) placeholder

The floating launcher has two shapes: `bubble`, a single chat button (today's only
behavior), and `capsule`, a vertical stack of three circular icons (issue #436).
Collapsed, a `capsule` widget looks identical to a `bubble` one: one chat button,
bottom right.
Clicking it opens the chat panel and expands the capsule, revealing a notebook icon
and an HPC placeholder; the chat button itself never moves.
Above 600px wide, the capsule expands upward into a vertical stack, and the chat
panel opens to the button's left instead of above it.
At 600px and narrower, the capsule instead expands into a row beside the chat
button, and the panel opens above it, exactly as it always has.
A community that never sets `launcher` renders exactly as it did before this field
existed, markup and computed styles included; that equivalence is asserted in
`frontend/test-widget-capsule.js`.

| Field | Type | Default | What it changes |
|---|---|---|---|
| `launcher` | `"bubble"` or `"capsule"` | `"bubble"` | The floating launcher's shape. |
| `launcher_label` | string, up to 40 characters, no `<`/`>` markup | none | Replaces the hardcoded collapsed-launcher tooltip, `Ask me about <title>`, with a shorter label. |

```yaml
widget:
  launcher: capsule
  launcher_label: "Explore NEMAR"
```

Both fields live on `WidgetConfig` and reach the frontend through `WidgetConfigResponse`,
the same path `theme_color` and `user_bubble_color` use; `resolve()` omits `launcher`
for the `bubble` default and omits `launcher_label` when unset, so an older or
unconfigured community sends neither key.
`launcher_label` only changes what shows *before* the click: the greeting
(`initial_message`), suggested questions and the rest of the panel stay behind it
either way.

### The three icons

Bottom to top: chat (today's button, unchanged), notebook, HPC.
Each is an accessible button with an `aria-label` naming its current state and a
hover/keyboard-focus tooltip in the same look as the collapsed launcher's own tooltip.
An icon that cannot be used right now carries `aria-disabled="true"`, never the
`disabled` attribute, so it stays focusable and a screen reader can still reach its
reason.
The active notebook icon is a themed surface, the same `theme_color`/`theme_text_color`
pair the launcher button above already uses; the inactive and coming-soon look is a
fixed neutral surface that never changes with a community's theme, which is what keeps
a disabled button from ever reading as active.

The HPC icon is a placeholder everywhere: always `aria-disabled`, always tooltipped
"HPC submission is coming soon", and always carrying a small "Soon" badge.
Submission to a cluster sits outside OSA (`nemarOrg/nemar-cli` ADR 0049) and is drawn
now only so the capsule does not need redesigning once it exists.

The notebook icon reflects the dataset on screen, from `setDataset` (below), in four
states:

| State | `aria-disabled` | Tooltip |
|---|---|---|
| No dataset (never set, or explicitly `null`) | `true` | Open a dataset page to start a notebook |
| A dataset, Zarr copy unknown | `true` | Checking whether this dataset has a Zarr copy |
| A dataset with no Zarr copy (`zarr: false`) | `true` | This dataset has no Zarr copy yet, so there is nothing to open in a notebook |
| A dataset with a Zarr copy (`zarr: true`) | `false` | Open `<id>` in a Python notebook (JupyterLite, opens a new tab) |

"Inactive" (no dataset, or no Zarr copy) and "coming soon" (HPC) read differently on
purpose: an inactive icon is muted; "coming soon" carries the badge on top of that
same muted look.
Neither is just a lower opacity on the active look, which would read as broken rather
than deliberate.

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
- Invalid input (a malformed `id`, or a `zarr` that is not `true`/`false`/absent) is
  ignored with a `console.warn`; it never throws, and it never changes the
  previously-set state.

`setDataset` can be called before `OSAChatWidget.init()` runs, since the embedder's own
dataset-detection script may load before or after the widget script.
The value is stored either way and rendered once the capsule exists; every later call
re-renders immediately.

Clicking the active notebook icon opens
`${notebookUrl}open.html?community=${encodeURIComponent(communityId)}&dataset=${encodeURIComponent(id)}`
in a new tab (`window.open(url, '_blank', 'noopener')`).
That URL is the notebook site's contract: it validates both parameters, writes a
starter notebook into JupyterLite's own storage, and redirects into it.

Testing: `frontend/test-widget-capsule.js` runs the real widget source in a happy-dom
window (the same technique `frontend/test-widget-tools.js` uses), and
`frontend/browser-harness/widget_e2e.py --nemar` serves NEMAR's real, capsule-enabled
config for a manual or scripted Chrome check (`widget-e2e-dataset.js` drives
`setDataset`/`setConfig` from the page's own URL, so a run does not need a devtools
console).

## Embedder `setConfig` keys

A page that embeds the widget directly (rather than only through a
community's own `config.yaml`) can set any of these through
`OSAChatWidget.setConfig({...})`, in camelCase.
A key set this way always wins over whatever the server sends for the same
field: once an embedder sets `themeColor`, no community config value for
`theme_color` can override it (`_userSetKeys` in `frontend/osa-chat-widget.js`).

| `config.yaml` field | `setConfig` key |
|---|---|
| `title` | `title` |
| `initial_message` | `initialMessage` |
| `placeholder` | `placeholder` |
| `suggested_questions` | `suggestedQuestions` |
| `logo_url` | `logo` |
| `theme_color` | `themeColor` |
| `theme_text_color` | `themeTextColor` |
| `accent_color` | `accentColor` |
| `user_bubble_color` | `userBubbleColor` |
| `user_bubble_text_color` | `userBubbleTextColor` |
| `launcher` | `launcher` |
| `launcher_label` | `launcherLabel` |

```html
<script src="https://demo.osc.earth/osa-chat-widget.js" data-no-auto-init></script>
<script>
  OSAChatWidget.setConfig({
    communityId: 'my-tool',
    themeColor: '#0b5fa5',
  });
  OSAChatWidget.init();
</script>
```

Two more embedder-only settings share the same malformed-value handling as
the colors above, but have no `config.yaml` counterpart: `disclaimerColor`
and `disclaimerBackground`, the AI-disclaimer banner's own text and
background colors (any valid CSS color, not only `#RRGGBB` hex).

A third, `notebookUrl`, has no `config.yaml` counterpart either, but is
validated as a URL rather than a color: the notebook site's base URL (see
"Launcher" above), an absolute `https:` URL or an `http:` URL on
`localhost`/`127.0.0.1` (for local testing), with or without a path.
Defaults to `https://notebook.osc.earth/osa/`; `/osa/` names this widget's
own project on the shared `notebook.osc.earth` plane. An accepted value is
normalized to end in a trailing slash; anything else is ignored with a
`console.warn`, keeping whatever was set before.

The "What happens with a malformed value" section right below covers only the color
fields; it does not describe `notebookUrl` (see above) or `launcher` (any value other
than exactly `"capsule"` behaves as `bubble`, silently, since `setConfig` does not
validate it the way it validates `notebookUrl`).

### What happens with a malformed value

Every color field above is validated twice: `WidgetConfig`'s own
`pattern=r"^#[0-9a-fA-F]{6}$"` refuses a malformed `config.yaml` value at
load time (before the community ever ships), and the widget checks again
before applying anything, in case an embedder's `setConfig` call carries a
typo `config.yaml` never saw. A value that fails the second check is never
applied silently: the widget logs a `console.warn` naming the field and the
rejected value, and falls back to that field's own default, exactly as if
the field had been left unset.
