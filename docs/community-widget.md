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
| `dataset_suggested_questions` | list of `{text, needs_zarr}`, at most 10 | empty list | Suggestions for a page that names a dataset with `setDataset`, in place of `suggested_questions`; see [Questions about the dataset on screen](#questions-about-the-dataset-on-screen). |
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

### Questions about the dataset on screen

A host page that names the dataset on screen with [`setDataset`](#osachatwidgetsetdatasetvalue)
can be offered questions about that dataset rather than the general list (#477).
Each entry of `dataset_suggested_questions` is a template:

```yaml
widget:
  dataset_suggested_questions:
    - text: "What is {dataset_id} about, and how was it recorded?"
    - text: "Plot 10 seconds of sub-{subject}'s {task} recording from {dataset_id}"
      needs_zarr: true
    - text: "How do I download {dataset_id}?"
```

- `text` is plain text, up to 200 characters, and must name its dataset with `{dataset_id}`,
  so the question the reader sends is about a dataset the model can look up.
  It may also use `{subject}` and `{task}`, the Brain Imaging Data Structure (BIDS) labels the page passes in `setDataset`;
  no other blank is accepted, and a config with one fails to load.
- `needs_zarr: true` shows the question only when `setDataset` says the dataset has a Zarr copy.
  Set it on a question that runs code against a recording, since the browser runtime reads recordings from Zarr.
- On the opening screen of a dataset page, the widget shows up to three templates, in this order,
  skipping one marked `needs_zarr` that the dataset cannot answer and one with a blank the page did not fill.
  When none fits, the general `suggested_questions` show instead,
  as they do on every page that names no dataset.
- Mid-conversation, a dataset the conversation has not been on yet gets a compact row of up to two,
  labeled with the dataset, above the input.
  The widget records the dataset on screen with each message the reader sends,
  so the row goes once the reader sends from that page and stays gone after a reload.
- A pop-out window is told the dataset its opener has on screen, and every later `setDataset` on the opener, so its suggestions follow the page.
- Questions are never written by the model: they are the community's own text, with the page's facts filled in.
- A community that sets none, which is every community but NEMAR today, keeps `suggested_questions` on every page.

A question that runs code should stay within what the community's browser runtime preloads
([`docs/community-browser-runtime.md`](community-browser-runtime.md)).
NEMAR's preloads numpy, matplotlib, zarr and eegprep-lean, not scipy,
so its power-spectrum question is answered with a numpy FFT.
Work that needs scipy belongs in the notebook, which can `%pip install` it.

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
| `accent_color` | hex `#RRGGBB` | `theme_color` itself | `theme_color`'s hue used as a foreground on the white panel: links, borders, focus rings, checkbox `accent-color`. The dark panel does not use it (see "Light and dark" below). |
| `user_bubble_color` | hex `#RRGGBB` | platform blue `#2563eb` | Surface: the reader's own message bubbles. |
| `user_bubble_text_color` | hex `#RRGGBB` | white `#ffffff` | Text in the reader's own message bubbles. |

### Contrast guidance

Every text-on-surface pair (`theme_text_color` on `theme_color`,
`user_bubble_text_color` on `user_bubble_color`, and `accent_color` on the
widget's white panel) needs at least a 4.5:1 contrast ratio under the Web Content Accessibility Guidelines (WCAG) 2, the same
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

## Light and dark

The widget draws a light panel unless its community asks for more (issue #469).

| Field | Type | Default | What it changes |
|---|---|---|---|
| `color_scheme` | `"light"` or `"auto"` | `"light"` | `"auto"` follows the reader's device setting (`prefers-color-scheme`), including a device that switches while the page is open. |

```yaml
widget:
  color_scheme: auto
```

`resolve()` omits `color_scheme` for the `"light"` default, so a community that never sets it sends no key, and its widget's markup is exactly what it was before this field existed:
no `osa-dark` class and no extra inline property, on a dark device too (`frontend/test-widget-color-scheme.js`).

### The host page's own choice: `OSAChatWidget.setColorScheme(value)`

A host page with its own theme switch passes the reader's choice on:

```js
OSAChatWidget.setColorScheme('dark');  // or 'light', or 'auto' to hand it back to the device
```

- It outranks the community's `color_scheme`, like any other embedder setting, and can be called before or after `OSAChatWidget.init()`.
- Called before `init()`, the panel opens in that scheme with no flash of the other one.
- It reaches an open pop-out too, and a pop-out opened afterward starts in it.
- `'dark'` works for any community, including one that never set `color_scheme`: a host page with a dark theme of its own may always ask for a dark widget.
- Anything other than `'light'`, `'dark'` or `'auto'` is ignored with a `console.warn` naming the accepted values; `setConfig({colorScheme})` takes the same three.
- A browser without `matchMedia` leaves `'auto'` light, with a warning; Safari before 14, whose media queries offer only `addListener`, still follows the device.

### What the dark panel changes

Every surface, text and border color has a dark value, as do code blocks, tables, the error and warning banners, the tool panels, Settings and the tooltips.
Raised surfaces (the panel, the launcher, the tooltips) also get a one-pixel edge, since a shadow alone vanishes against a dark host page.

Two configured colors give way on the dark panel, because they were chosen to read on white:

- **`accent_color`.** The dark panel uses `theme_color` itself as its foreground.
  It is measured on both dark surfaces an accent is read on, the panel (`#111827`) and the assistant's bubble (`#1f2937`), where links sit.
  NEMAR's teal reads at 8.0:1 and 6.6:1, where `#257a92` would be 3.6:1 and 3.0:1.
  A `theme_color` under 4.5:1 on either is mixed with white, in 10% steps, until it reads on both; the platform blue becomes `#6692f1` (5.9:1 and 4.9:1).
  Every color reads before it reaches white: black stops at `#999999`.
- **`disclaimerColor` and `disclaimerBackground`.** The dark panel's disclaimer uses its own amber pair.

`theme_color`, `theme_text_color` and the reader's bubble colors are unchanged: they are surfaces with their own text, and read the same on either panel.

### What every community gets

Before this change, a dark host page could darken parts of any community's light panel:
the browser drew the chat input, the Settings fields and the scrollbars in the host's dark scheme,
and text with no color of its own took the host page's light text color.
Every widget now declares `color-scheme: light` (or `dark` under `osa-dark`) and gives its own text and fields explicit colors.
On a light host page the only visible difference is that typed text, and any panel text that used to inherit the host page's color, is the widget's own near-black `#1f2937`.

Testing: `frontend/test-widget-color-scheme.js` runs the real widget source in a happy-dom window,
and `frontend/browser-harness/color-scheme-check.mjs --serve` checks the browser's own drawing of the fields in Chrome, against a dark host page (see the harness README).
Both run in CI.

## Launcher: the capsule, the notebook icon, and the high-performance computing (HPC) placeholder

The floating launcher has two shapes: `bubble`, a single chat button (today's only
behavior), and `capsule`, a vertical stack of three circular icons (issue #436).
Collapsed, a `capsule` widget shows one chat button, bottom right, as a `bubble` one
does, drawn at 58px so it is easy to see (issue #490).
Clicking it opens the chat panel and expands the capsule, revealing a notebook icon
and an HPC placeholder, and the chat button settles to 46px, the size of the other
circles; closing the panel grows it back.
Its bottom-right corner stays 20px from the window's edges throughout, where the
bubble's is, so the chat button itself never moves.
The notebook opens as a second tab of the same panel (issue #470, "The notebook tab"
below), and the capsule shows which tab is open.
Above 600px wide, the capsule expands upward into a vertical stack, and the chat
panel opens to the button's left instead of above it.
At 600px and narrower, the capsule instead expands into a row beside the chat
button, and the panel opens above it, exactly as it always has.
A community that never sets `launcher` renders exactly as it did before this field
existed, markup and computed styles included; that equivalence is asserted in
`frontend/test-widget-capsule.js`.

| Field | Type | Default | What it changes |
|---|---|---|---|
| `launcher` | `"bubble"` or `"capsule"` | `"bubble"` | The floating launcher's shape. `capsule` requires a top-level `notebook` section (`docs/community-notebook.md`); the loader refuses the config without one, since the notebook icon would open a tab that can only report an error. |
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

Bottom to top: chat, notebook, HPC.
With the panel open, every circle is 46px, about 15% larger than the panel's 40px Send button.
At rest the chat circle is 25% larger, 58px with a 26px icon.
It is drawn larger rather than laid out larger, with CSS `scale` and `translate`, so its box stays 46px.
Nothing else in the capsule depends on whether the panel is open: not the indicator, not the pill, and not the panel's place beside it.
Its bottom-right corner stays 20px from the window's edges while it resizes, and while it is hovered at rest, when it grows 5% as every launcher does.
A browser without those two properties draws it at 46px at rest too.
The collapsed launcher's tooltip sits 10px to the left of the 58px circle, centered on it.
Each is an accessible button with an `aria-label` naming its current state and a
hover/keyboard-focus tooltip in the same look as the collapsed launcher's own tooltip.
An icon that cannot be used right now carries `aria-disabled="true"`, never the
`disabled` attribute, so it stays focusable and a screen reader can still reach its
reason.

The open tab's circle sits on a filled indicator in `theme_color`, which slides to
the other circle when the tab changes; that circle is `aria-pressed="true"`.
A circle that can be clicked but is not the open tab is outlined in the accent color.
The inactive and coming-soon look is a fixed neutral surface that never changes with
a community's theme, which is what keeps a disabled button from ever reading as active.

Opening the panel grows the capsule's pill out of the chat button, the chat button
shrinks from 58px to 46px over 280ms, and the notebook and HPC circles arrive 30ms and
70ms after the pill; the views, the header's title and the indicator animate between tabs.
With `prefers-reduced-motion: reduce`, every change is immediate except a short plain
fade between the views, and nothing slides, scales or turns: the chat button is 58px
or 46px, never in between.

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
| A dataset with a Zarr copy (`zarr: true`) | `false` | Open `<id>` in a Python notebook |

"Inactive" (no dataset, or no Zarr copy) and "coming soon" (HPC) read differently on
purpose: an inactive icon is muted; "coming soon" carries the badge on top of that
same muted look.
Neither is just a lower opacity on the active look, which would read as broken rather
than deliberate.

### `OSAChatWidget.setDataset(value)`

The embedding page tells the widget which dataset, if any, is on screen:

```js
OSAChatWidget.setDataset({ id: 'nm000132', zarr: true });
OSAChatWidget.setDataset(null); // not a dataset page
```

- `value` is `null` (not a dataset page) or an object `{ id, zarr, subject, task }`.
- `id` must match `^[A-Za-z0-9._-]{1,64}$`.
- `zarr` is `true` (a Zarr copy exists), `false` (it does not), or absent/`undefined`
  (not known yet, e.g. the check is still in flight).
- `subject` and `task` are optional BIDS labels, without the `sub-` or `task-` prefix (`001`, `N170`),
  matching `^[A-Za-z0-9]{1,64}$`: the recording the page would point a reader at first.
  They fill the `{subject}` and `{task}` blanks of the dataset questions above (#477),
  and nothing else reads them.
- Invalid input (a malformed `id`, or a `zarr` that is not `true`/`false`/absent) is
  ignored with a `console.warn`; it never throws, and it never changes the
  previously-set state.
  A `subject` or `task` that is not a label is dropped alone, with a `console.warn`, and the rest of the call applies:
  it only fills question blanks, so it should not keep the previous dataset, and its notebook icon, on screen.

`setDataset` can be called before `OSAChatWidget.init()` runs, since the embedder's own
dataset-detection script may load before or after the widget script.
The value is stored either way and rendered once the capsule exists; every later call
re-renders immediately.

### The notebook tab

Clicking the available notebook icon opens the panel on its Notebook tab (issue #470),
or switches to it if the panel is already open on chat.
The tab is a frame at
`${notebookUrl}open.html?community=${encodeURIComponent(communityId)}&dataset=${encodeURIComponent(id)}`,
the notebook site's contract: it validates both parameters, writes a starter notebook
into JupyterLite's own storage, and redirects into it.
The notebook site decides which pages may frame it (`docs/adr/0012-the-notebook-as-a-widget-tab.md`).
The chat circle goes back to chat, and the open tab's own circle, or the header's
close button, closes the panel.

- **The header follows the tab.** It reads "Notebook", with a status line naming the
  dataset: `Opening the notebook…`, `Starting Python…`, `Python ready` (or `Ready` for
  a starter with no setup cell), `Setup did not finish; see the notebook`, or
  `The notebook did not open here`.
  The chat's own header buttons (Settings and reset) are hidden on the notebook tab;
  the close button stays, and so does the pop-out button, which opens the pop-out on
  the notebook tab (see "The pop-out window" below).
- **The frame is kept.** Going back to chat hides it without unloading it, so its
  Python keeps running and returning finds the notebook as it was left.
  A new dataset on screen replaces the frame; a dataset with no Zarr copy drops it
  and takes the panel back to chat.
- **The notebook reports its progress.** `notebook/osa-bridge.js`, inside the frame,
  says when it is ready and how its setup cell went; the widget listens only to
  messages from the frame it made and from the notebook site's origin.
  While Python is starting and the reader is on chat, a ring turns on the notebook
  circle.
- **The theme follows the widget.** The widget sends its light or dark scheme to the
  notebook when the frame loads, when the notebook says it is ready, and whenever the
  scheme changes, addressed to the notebook site's origin only.
- **A notebook that cannot open here says so.** A frame that has loaded but not
  reported ready within 12 seconds, or has not reported ready within 45 seconds of
  being created, or whose bridge reports a startup error before it is ready, is
  covered by a message with "Try again" (a fresh frame) and "Open in a new tab" (the
  same address in a browser tab of its own).
  The usual cause is the host page's own policy, below.
  The failed frame is kept: coming back to the tab shows the same message rather than
  starting over, and the message goes away if a notebook that was only slow reports
  ready after all.
  A notebook that failed, or whose setup cell did not finish, while the reader is on
  chat puts a small red dot on the notebook circle, and its tooltip says which.
  The widget also logs to the console a theme the notebook could not apply, and any
  message from the notebook it does not recognize.
- **Resizing.** The capsule's panel resizes up to 1400px wide, stopping 120px short of
  the window's left edge, and to the window's full height less 40px; it can always
  reach a bubble's 600 by 800, even on a window too small for those margins.

**A host page with a Content Security Policy (CSP)** must allow the notebook host in
`frame-src` (or, lacking `frame-src`, in `child-src` or `default-src`):
`https://notebook.osc.earth`, or `https://develop-notebook.osc.earth` for a page that
points `notebookUrl` at the develop notebook.
Without it the browser refuses the frame, and the tab shows the fallback above.

Testing: `frontend/test-widget-capsule.js` and `frontend/test-widget-notebook-tab.js`
run the real widget source in a happy-dom window (the same technique
`frontend/test-widget-tools.js` uses), and both run in CI.
`frontend/browser-harness/widget_e2e.py --nemar` serves NEMAR's real, capsule-enabled
config for a manual or scripted Chrome check (`widget-e2e-dataset.js` drives
`setDataset`/`setConfig` from the page's own URL, so a run does not need a devtools
console), and `frontend/browser-harness/notebook-tab-check.mjs` drives the notebook
tab in Chrome against the live develop notebook; it needs the network, so it is not
in CI.
`frontend/browser-harness/first-paint-check.mjs` (below) also hovers and presses NEMAR's
resting chat button in Chrome with a real pointer, including on the part of the 58px
circle outside its 46px box, and opens and closes the panel at 1440px and 390px wide,
sampling the chat button on every frame: 58px to 46px and back, with its corner 20px
from the window's edges in every frame.

## The pop-out window

The header's pop-out button opens the widget in a window of its own, which the panel fills.

- **It carries the page's widget.** Its settings, every `setConfig` the page made included, the color scheme the host page chose, if it chose one, and the dataset on screen.
  A later `setColorScheme` or `setDataset` on the page reaches an open pop-out too, including one whose script is still loading.
- **It shares the page's storage.** The pop-out is an `about:blank` window of the page's own origin,
  so it has the page's chat history and browser runtime workspace (`docs/community-browser-runtime.md`).
- **A capsule community's pop-out has the panel's tabs** (issue #470), Chat and Notebook, as a strip under its header, since a pop-out has no launcher.
  It opens on the tab the reader was on when they clicked the pop-out button, drawn there at once.
  The strip's notebook tab follows the page's dataset as the notebook circle does:
  it cannot open without a dataset that has a Zarr copy, and its tooltip says why;
  it shows the circle's busy and attention cues as a small mark while the reader is on chat.
- **The pop-out's notebook is a frame of its own, and a fresh notebook session.**
  Python starts again there, and so does the starter's setup cell.
  The notebook opens with the edits that had reached storage, which an edit does within about five seconds (`docs/community-notebook.md`).
  The page's own notebook frame is left as it was, still running, so both windows have the same stored notebook open: edit it in one of them at a time.
- **A bubble community's pop-out is unchanged**: the chat only, with no tab strip.

### What a host page's policy needs for the pop-out

Nothing beyond what the widget itself needs.
The pop-out is an `about:blank` window of the page's own origin, so the browser applies the page's own CSP to it,
and the pop-out runs no inline script:
its document is written without one, its settings are set on its window from the page,
and the widget arrives as a script element with the address of the page's own widget tag,
and that tag's `integrity` and `crossorigin`, when it has them.
A policy that lets the page load the widget lets the pop-out load it too, and `script-src` needs no `'unsafe-inline'`.
Until issue #470, the pop-out wrote the widget's source into itself as inline script,
and opened blank on a page whose policy did not allow `'unsafe-inline'`.

- Keep `integrity` and `crossorigin` together on a pinned widget tag, as a page must for the widget itself:
  the pop-out copies both, and a script from another origin (jsDelivr, for nemar.org) pinned by its Subresource Integrity (SRI) hash loads only with `crossorigin`,
  since without it the browser cannot read the response to check the hash, and refuses it.
- `data-no-auto-init` on the tag is fine: the pop-out's copy does not carry it, and starts itself.
- The pop-out's document has a `<style>` element, and the widget adds its own styles as one,
  so the pop-out needs only the `style-src 'unsafe-inline'` the widget already needs.
- The notebook tab in the pop-out needs the same `frame-src` as the panel's (above).
- A pop-out whose script the browser refuses says so in its window ("The assistant could not load in this window"),
  and the page's console names the address it could not load.
- A policy that allows scripts only by nonce is not supported, here or for the widget as a whole:
  the widget adds script elements of its own, the pop-out's and the browser runtime's, without one.
  Allow the widget's host in `script-src`, as the policy above does.

Testing: `frontend/test-widget-popout.js` runs the real widget source in happy-dom windows, a host page and the pop-out it opens,
with the widget loaded by its script tag in both,
and `frontend/browser-harness/popout-check.mjs --serve` opens the pop-out in Chrome under a policy without `'unsafe-inline'`,
from both tabs, on a plain and a pinned widget tag (see the harness README); both run in CI.

## A run in the chat

A community whose model runs code in the reader's browser (`execute_code`, [`docs/community-browser-runtime.md`](community-browser-runtime.md)) shows each run in the reply that asked for it.
A community without it never has a run, and renders none of this.

Each run is two disclosures, one under the other, joined by a rule down their left (issue #491):

- **The run**, titled by how it ended and what the model said it does (`Ran Python: plot ten seconds of Cz`): its printed output, its figures, and "Edit and run".
  It opens by default when the run drew a figure, when the reader ran it, or while its editor is open, as it always has.
- **Its code**, labeled with its length (`Code · 14 lines`) and closed until the reader opens it, so the code opens without the output, and a figure is not pushed down the page by the script that drew it.
  It stays open while the reply keeps updating; a reload shows it closed again.

The code's bar has two buttons:

- **Copy** puts the code on the clipboard exactly as the run's record holds it, not the highlighted text on the page, and shows "Copied" for two seconds.
  Where the page may not use the Clipboard API (an insecure page, a frame without `clipboard-write`, or a browser that refuses), it tries the browser's older copy command;
  where that fails too, it opens the code, selects it, and says which keys copy it.
  It never opens a browser dialog.
- **Download** saves the code as a `.py` file built in the page, named for the dataset the question was about and the run's place in the conversation, counted from 1: `nm000132-run-3.py`.
  The dataset is the one the page named with `setDataset` when the reader sent the question; a question sent from a page that named none takes the community's id instead (`nemar-run-3.py`).
  The count is of every run the conversation shows, one that was declined, stopped, timed out or ran out of memory included.
  The workspace download numbers its `run-NNN` files differently: per chat session, and only the runs it saved files for, which are the runs whose Python ran to an end, successfully or with an error, in a browser that could store them (`ClientToolController#persist`, `frontend/osa-controller.js`).
  So the two numbers agree only while every run has ended that way.

The pop-out window (above) shows each run's code the same way, with the same Copy and Download; it rebuilds the conversation from storage, so a code block the reader opened on the page is closed there, as after a reload.

The permission gate ("Run this Python in your browser?") has the same Copy over the code it asks about, and copying it neither runs nor denies it; Download waits until the code has run.

Each figure a run shows has a **Download** button over its top-right corner (issue #492), which saves that figure's PNG, byte for byte, as `nm000132-run-3-figure-1.png`: the run's name, then the figure's number among the ones the run shows, counted from 1, as the workspace names a run's `figure-K.png`.
It is a real button, reached with Tab and pressed with Enter or Space, and its label names the figure and the file (`Download figure 1 as nm000132-run-3-figure-1.png`), as the figure's own alternative text numbers it (`Figure 1 produced by the code`).
It keeps its light colors on the dark panel, where the figure stays white.
A figure under an open "Edit and run" editor downloads as the reader's run it came from, not the run whose editor it is under.
Figures live only in the page's memory (they are never stored), so a run read back after a reload shows none, and has none to download.

Both copy and download the code the run's record keeps, its first 20,000 characters; the workspace download in Settings keeps every script whole ([`docs/community-browser-runtime.md`](community-browser-runtime.md), "Workspace").
The code is the model's, so it is escaped wherever it is shown, highlighted or not.

Testing: `frontend/test-widget-tools.js` runs the real widget source in a happy-dom window:
the two disclosures and their markup for hostile code, Copy through happy-dom's own clipboard, when it refuses and without it, Download's Blob, file name and freed address, the gate's Copy, the code's open state across a re-render, and a reply that ran no code rendering none of it;
and each figure's Download, its label and numbering, and its file's bytes and name.
`frontend/test-widget-popout.js` checks the code block, Copy and Download in the pop-out.

## The first paint: remembering the community's look

The `widget:` block reaches the page with the community config request, some
hundreds of milliseconds after the widget script runs.
Until then the widget has only its built-in defaults, so it used to draw the
launcher in the platform blue, at the bubble's 56px, and then change: to the
community's colors, and for a capsule community, to the capsule (issue #475).
It no longer does, for any community:

- **The look is remembered.** Each time the config arrives, the widget keeps its
  `widget` block, exactly as the server sent it, in the page's own `localStorage`
  under `osa-widget-config-<communityId>`, with the API endpoint it came from.
  The next load applies it before anything is drawn, through the same checks as a
  fresh config, so the launcher appears in the community's look from the first
  frame.
  The fresh config still arrives and wins, for every field: the widget first goes
  back to its built-in defaults and then applies it, because the server leaves a
  default out (a bubble launcher, the light scheme, an unset color), and a field
  it leaves out has to end at its default, not at what the last load remembered.
  That includes the launcher's shape: a remembered capsule whose fresh config is
  a bubble goes back to exactly the bubble's markup.
  So a community that changes its look is drawn in the old one on each reader's
  next load, until that load's config arrives, and in the new one after that.
  An embedder's own `setConfig` keys outrank both, as they outrank a fresh config.
  A remembered entry for another API endpoint, or one that cannot be read, is
  ignored.
- **A first visit waits for the config.** With nothing remembered, the launcher
  stays hidden until the config arrives, and nothing animates while it waits, so
  it appears already in the community's look.
  If the config takes longer than 1.5 seconds, or the request fails, the launcher
  is shown in the defaults then, as before.
  The launcher's tooltip appears 1.5 seconds after the launcher does.
  The pop-out has no launcher, so it never waits.

Storage that is blocked or full costs only the remembering: the widget warns in
the console, and every load behaves as a first visit.
An invalid remembered color is refused and warned about as a fresh one is, once
per field and value however many times the config is applied.

Testing: `frontend/test-widget-first-paint.js` runs the real widget source in a
happy-dom window, and `frontend/browser-harness/first-paint-check.mjs --serve`
records the launcher on every animation frame in Chrome, with the config request
held for 600 ms, for NEMAR on a light and a dark device and for a bubble community
on a dark device; both run in CI.

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
| `dataset_suggested_questions` | `datasetSuggestedQuestions` (the same `{text, needs_zarr}` entries) |
| `logo_url` | `logo` |
| `theme_color` | `themeColor` |
| `theme_text_color` | `themeTextColor` |
| `accent_color` | `accentColor` |
| `user_bubble_color` | `userBubbleColor` |
| `user_bubble_text_color` | `userBubbleTextColor` |
| `launcher` | `launcher` |
| `launcher_label` | `launcherLabel` |
| `color_scheme` | `colorScheme` (also `'dark'`; see `setColorScheme` above) |

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
