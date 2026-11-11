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
| `search_paths` | list of paths, at most 5, each starting with `/`, at most 200 characters, with no query, fragment or whitespace | empty list | Pages where the reader is searching for datasets rather than viewing one: a page whose path is one of these, or below one at a path-segment boundary, gives the chat the search context (`/` is the home page alone); see [A context change starts a new chat](#a-context-change-starts-a-new-chat). |
| `center_start` | `{capabilities, note}` | none | The center-stage dialog's start screen: up to three `{title, text}` cards and a footer note; see [The start screen](#the-start-screen). |
| `analysis_docs` | `{title, url}`, an `https://` address | none | A docs page linked from the start screen's footer and from Settings; see [The start screen](#the-start-screen). |
| `privacy_url` | string, an `https://` address of at most 500 characters | the platform's own privacy page | The page the **Privacy choices** dialog links to; see [Privacy choices](#privacy-choices). |
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
NEMAR's preloads numpy, SciPy, matplotlib, zarr and eegprep-lean,
so its power-spectrum question is answered by reading the recording in pieces sized by the reader's memory budget and by welch's limit of `2**28 // nperseg` samples,
calling `scipy.signal.welch` on one channel of a piece at a time,
and averaging the estimates over the pieces by segment count,
which is the spectrum of the whole recording,
since past either limit it raises "array is too big" in 32-bit WebAssembly
(see "SciPy in 32-bit WebAssembly" in `docs/community-browser-runtime.md`),
and its ERP question low-passes with `scipy.signal.butter` and `sosfiltfilt`.
A package outside that list belongs in the notebook, which can `%pip install` it.

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
Everything on this page that says bottom right, left or right of the launcher, or a fixed distance from the window's edges, describes the defaults:
"Position, size and offsets" below says how `launcher_position`, `launcher_size` and the offsets change them, and everything is mirrored when the launcher is on the left.

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

### Position, size and offsets

Where the launcher sits and how big it is are set in the same `widget:` block (issue #553).
All of it is optional, and a community that sets none renders exactly as it did before these fields existed.
The launcher's sizes, distances and side described elsewhere in this document (58px closed and 46px open for the capsule, 56px for the bubble, 20px from the window's edges, bottom right, the capsule's panel to its left) are the defaults, and the fields below change them.
The 600px breakpoint does not change.

| Field | Values | Default | What it changes |
|---|---|---|---|
| `launcher_position` | `"bottom-right"` or `"bottom-left"` | `"bottom-right"` | Which bottom corner of the window the launcher, its label and the chat panel are in. The panel stays anchored to the bottom edge and grows upward, so there is no top position. |
| `launcher_size` | whole pixels, 44 to 96 | 56 for a bubble, 58 for a capsule | The launcher's diameter while the panel is closed. |
| `launcher_open_size` | whole pixels, 44 up to the closed size (`launcher_size`, or 56 for a bubble and 58 for a capsule when that is unset) | 80% of `launcher_size`, rounded and never below 44, when that is set; with neither set, a capsule's 46 and a bubble's 56 (no change) | The diameter while the panel is open. The launcher shrinks to it when the panel opens and grows back when it closes, with the animation the capsule has always had, and for a bubble too. Equal sizes mean no change. Setting `launcher_size` alone makes a bubble shrink, even to its own 56px (it opens at 45); to make a bubble larger without shrinking it, set both sizes to the same number. |
| `launcher_offset_x` | whole pixels, 0 to 200 | 20 | How far the launcher is from the side edge of the window, the side `launcher_position` names. |
| `launcher_offset_y` | whole pixels, 0 to 200 | 20 | How far the launcher is from the bottom edge. Raise it to clear something fixed at the bottom of the page, such as a documentation site's version menu or social buttons. |
| `launcher_mobile_offset_x`, `launcher_mobile_offset_y` | whole pixels, 0 to 200 | the two offsets above | The offsets at 600px wide and narrower, the width at which the capsule turns into a row. Each falls back to its own desktop offset, so setting only `launcher_mobile_offset_y` lifts the launcher on a phone and changes nothing else. |

```yaml
widget:
  launcher_position: bottom-left
  launcher_size: 72              # opens at 58 (80% of 72, rounded)
  launcher_offset_y: 96          # clear of a footer bar along the bottom of the page
  launcher_mobile_offset_y: 72   # and, on a phone, of its own controls
```

An offset is a distance from an edge, and the panel and the label move with the launcher, keeping the same gaps around it.
The position applies at every width; the mobile offsets are the only thing that changes at 600px and narrower.
The sizes are the drawn diameters: a launcher's box is always its open size, and closed it is drawn larger with CSS `scale` and `translate`, so nothing reflows while it changes and its corner at the anchor holds still through the whole animation, at either side.
The floor of 44px is the size in WCAG 2.2's enhanced target-size criterion (2.5.5); a smaller launcher is refused when the community is loaded.
Under `prefers-reduced-motion` the launcher goes between its two sizes at once.
The panel keeps inside the window however far the launcher is moved in: its minimum width and height give way to the room the offsets leave, so on a 320px-wide phone a launcher 30px in still has a panel that fits, a little narrower than usual.

The numbers must be whole numbers.
`WidgetConfig` refuses a value outside these ranges, an open size larger than the closed one, and a float, a quoted number or a boolean, when the community loads, with a message naming the field.
A bare `yes`, `no`, `on` or `off` in YAML is a boolean, so it is refused rather than turned into a 1px or 0px offset.
A leading zero is not: YAML reads `020` as the octal 16, so write `20`.

The widget makes the same checks on a page's `setConfig` (see "Embedder `setConfig` keys" below).
A value outside a range is ignored with a `console.warn` naming the field, and the community's own value, if it has one, still applies.
`undefined` is not a value, and is not warned about.
`null` is a value: it puts the setting back to its default over the community's.
The limits are in `src/core/config/community.py` and again in `LAUNCHER_LIMITS` in the widget, and `tests/test_frontend/test_widget_drift.py` fails if they differ, or if the stylesheet's own fallbacks (the defaults) do.
`frontend/test-widget-launcher-geometry.js` tests the behavior in a DOM, and `frontend/browser-harness/launcher-geometry-check.mjs` checks the result in Chrome, on every animation frame.

### The three icons

Bottom to top: chat, notebook, HPC.
With the panel open, every circle is 46px, about 15% larger than the panel's 40px Send button.
At rest the chat circle is about a quarter larger, 58px with a 26px icon.
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

- `value` is `null` (not a dataset page) or an object `{ id, title, zarr, subject, task }`.
- `title` is optional, a string of 1 to 200 characters: the dataset's name, shown beside its id in the
  [center-stage dialog's header](#the-dialog) and nowhere else.
  One that is not a string of that length is dropped alone, with a `console.warn`.
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
  the window's far side (the left edge, or the right edge when the launcher is on the
  left), and to the window's full height less 40px; it can always
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

Every stage of a run is one **run card** (issues #664 and #672; the builders are the `// --- Run card ---` block in `frontend/osa-chat-widget.js`).
A card never reads page state, so one restored from storage renders the same before the runtime bundle has loaded as after.

- **Asking.** "Run this Python in your browser?", the assistant's description, the code in view with Copy, and Run, Don't run and "Run without asking until I reload".
- **Running.** "Running Python", what the runtime is doing with a progress bar once the boot reports a step, and Stop.
- **Done.** The card is titled by how the run ended (`Ran Python`, `Python raised an error`, `Not run`, `Stopped: took too long`, `Stopped`, `Stopped: out of memory`; `Your run: ` is added for a run the reader started), with the model's description under it.
  Then, in this order:
  the figures, visible by default, each with its **Download** (below);
  a **Show code · N lines** button, the card's one prominent control (accent colored, bordered, with `aria-expanded` and `aria-controls` naming the code's panel), which reveals the code and becomes **Hide code · N lines**;
  **Copy code**;
  **Output · N lines**, which reveals what the code printed;
  and **Edit and run**, whose editor opens inside the card.
  The code, the output and every other panel are closed until the reader asks, except the output of a run the reader started themselves.
  A choice survives a re-render (a streamed chunk, runtime progress) because it is kept on the run's record (`_codeOpen`, `_panelsOpen`); it is never stored, so a reload shows every panel closed.
  Figures are never stored either, so a restored card is the same card without its figures, and leaves no placeholder where they were.

### When a run fails

A run that raised an error, timed out or ran out of memory opens its card by default (issue #672):

- **What went wrong**, as `Type: message`, from the run's stderr, where the runtime writes the exception's type and message and then, when it knows the line, `  line N: code` (`_error_text`, `frontend/osa-output.js`).
  An error the runtime found no line for shows the type and message alone, and no line is invented.
  A run the runtime stopped shows its one `[runtime]` sentence.
  Stderr that matches none of this is shown whole.
- **The failing line**, marked in the code around it (the line, the one before and the one after), with a `>` and a hidden "Failing line" label as well as its color.
- **All code · N lines**, **Full traceback** and **Copy error**.
  The traceback is the one the browser kept for `get_full_output`; it is stored on the run (`traceback`, cut to 4,000 characters like the other output) and the button is not drawn for a run that has none.
  Copy error puts the error as written, `Type: message` and the failing line, on the clipboard and says "Copied".
  Where the browser refuses the clipboard, a block holding exactly that text is shown and selected, and the card names the keys that copy it.
- **A retry line** on the last run of a reply that is still waiting on the assistant after the failure: "The assistant is looking at this error and may run the code again (K of 20 runs used)", where 20 is the most runs one reply may make. It says "may" because the assistant can also answer in prose, and the line goes when its text starts.
  It is gone once the reply has finished, and is never shown for a run the reader started, which the assistant does not see.

A run that worked and warned shows **N warnings**, collapsed, counting the lines of its stderr that hold `Warning:`; stderr with no warning in it shows nothing, as before.

Output that was cut says so: "Output was cut: N characters not shown."
The runtime cuts a stream from its middle (`... N characters omitted ...`, by default at 16,384 characters of stdout and 8,192 of stderr; a community's `stdout_chars` and `stderr_chars` can change them), and the widget keeps at most 4,000 characters of each of stdout, stderr and the traceback.
It cuts those from the middle too, keeping both ends, because a run's error is written at the end of its stderr; its own marker adds the runtime's count to what it dropped, so the number is of everything the run wrote that is not in the card.

### Copy and Download

- **Copy code** puts the code on the clipboard exactly as the run's record holds it, not the highlighted text on the page, and the button says "Copied" for two seconds (the card's status line, a live region, says it for a screen reader).
  Where the page may not use the Clipboard API (an insecure page, a frame without `clipboard-write`, or a browser that refuses), it tries the browser's older copy command;
  where that fails too, it opens the code, selects it, and says which keys copy it.
  It never opens a browser dialog.
  The permission gate's Copy, an icon over the code it asks about, and a failed run's, in the code's panel, work the same way, and copying neither runs nor denies anything.
  They are not a second copy of the logic: every copy in the widget, the reply's own buttons below included, goes through the same function.
- **Download** in the code's panel saves the code as a `.py` file built in the page, named for the dataset the question was about and the run's place in the conversation, counted from 1: `nm000132-run-3.py`.
  The dataset is the one the page named with `setDataset` when the reader sent the question; a question sent from a page that named none takes the community's id instead (`nemar-run-3.py`).
  The count is of every run the conversation shows, one that was declined, stopped, timed out or ran out of memory included.
  The workspace download numbers its `run-NNN` files differently: per chat session, and only the runs it saved files for, which are the runs whose Python ran to an end, successfully or with an error, in a browser that could store them (`ClientToolController#persist`, `frontend/osa-controller.js`).
  So the two numbers agree only while every run has ended that way.

Each figure a run shows has a **Download** button in a row under it, right-aligned (issue #492), never over the figure, where a legend often sits; it saves that figure's PNG, byte for byte, as `nm000132-run-3-figure-1.png`: the run's name, then the figure's number among the ones the run shows, counted from 1, as the workspace names a run's `figure-K.png`.
It is a real button, reached with Tab and pressed with Enter or Space, and its label names the figure and the file (`Download figure 1 as nm000132-run-3-figure-1.png`), as the figure's own alternative text numbers it (`Figure 1 produced by the code`).
A figure under an open "Edit and run" editor downloads as the reader's run it came from, not the run whose editor it is under.

The pop-out window (above) shows each run's card the same way; it rebuilds the conversation from storage, so a panel the reader opened on the page is closed there, as after a reload.

Both copy and download the code the run's record keeps, its first 20,000 characters; the workspace download in Settings keeps every script whole ([`docs/community-browser-runtime.md`](community-browser-runtime.md), "Workspace").
The code is the model's, so it is escaped wherever it is shown, highlighted or not, and so are the description, the output and the stderr.

Testing:
`frontend/test-widget-run-card.js` runs the real widget source in a happy-dom window: every state of the card (asking, running, done with and without a figure, each status), Show code, Copy code and Copy error on a real clipboard, Edit and run inside the card, the card after `saveHistory`, `loadHistory` and `renderMessages` with nothing transient stored, hostile content in every state, and the failures above from stderr recorded off real Pyodide (`frontend/test-support/run-card-fixtures.js`, re-recorded by `frontend/test-support/record-run-card-fixtures.js`).
`frontend/test-widget-tools.js` holds the markup for hostile runs, Copy's fallbacks, Download's Blob, file name and freed address, each figure's Download, and the contrast of the card's buttons;
`frontend/test-widget-color-scheme.js`, the card's dark colors;
`frontend/test-widget-popout.js`, the card in the pop-out.
`frontend/browser-harness/run-card-check.mjs [screenshot-dir]` draws every state at 400 px in headless Chrome, light and dark, checks the layout (inside the panel, nothing scrolls sideways, every button 36 px tall) and writes a screenshot of each.

### Copying from a reply

A reply's code blocks and the whole reply have a copy button too (issue #665), and they copy the way the run card does:
the Clipboard API, then the browser's older copy command, then the text selected in place with the keys that copy it named.
A copy that worked turns the button into a check with "Copied" beside it for two seconds, and the widget's status announcer (one polite live region, kept outside the conversation) says "Copied" for a screen reader.
A block's button copies the block's code as written; the reply's button copies the reply's markdown source.
Where the browser refuses both ways, a code block's code is selected where it stands, and the reply's source is shown in a block under the reply and selected there, since the page as drawn is not what the button promised;
a line says "This page cannot copy for you: the text is selected, so press Ctrl+C" (Cmd on an Apple platform), and the announcer says it too.
The reference on a failed request's banner copies the same way.
`frontend/test-widget-copy.js` runs the real widget through all three paths for each button.

### Image references and links in bold

A reply is rendered from markdown by the widget itself, never as raw HTML (issue #666).
The widget never loads an image a reply names, since the address is the model's and the server behind it is somebody else's.
`![Welch PSD](https://example.org/psd.png)` therefore renders as its alt text, "Welch PSD", in a span (`osa-image-reference`), with no `img`, no link and no address;
and when the reply's message has a figure from a run, as "Welch PSD (see the figure above)", pointing to the run card's own figure.
The pointer is shown only for a figure drawn by the same message's run, so a model-invented image never claims a figure that is not there; a figure from an earlier message gets none.
An image with no alt text renders as the pointer alone, or as nothing when there is no figure to point to.
A linked image, `[![badge](https://example.org/b.svg)](https://example.org)`, renders as a link whose text is the alt text (no pointer inside a link), and an image address may hold one level of balanced parentheses (`y_(1).png`).
Bold and italic render their content as markdown again, so `**[text](https://example.org)**` is a link inside bold, and code and image references inside them render too.
Every address still goes through the same check (`http:` and `https:` only), so `**[x](javascript:alert(1))**` renders its text and no link.
`frontend/test-markdown.js` runs the shipped functions over these cases, and `frontend/test-widget-run-card.js` checks the figure pointer on the real widget.
Highlighting a reply's code blocks (#598) and the host page's CSS reaching a reply (#597) are separate work.

## What a pending reply says it is doing

While a reply is pending the widget names what it is doing, from the tool's name alone (issues #538 and #494):
"Thinking..." while the model reasons, "Writing code..." from the moment the model starts composing a call to run code until the browser is asked to run it,
a readable label for a server tool from the moment its call is written ("Searching datasets...", "Looking up events...", "Looking up documentation..."),
"Running Python in your browser..." in the run card while the code runs,
and "Analyzing results..." while the model reads a tool's or a run's result, including while the result is on its way back on `/chat/resume`.
A tool whose name the widget cannot read is "Working...", and a plan being written is "Proposing plan...".
Before the reply has any text the status is the loading bubble's label; once it has text, a later activity is a line under that text, with the accent-colored dot and 12.5 px text of board A3 (the run card's retry line is drawn the same way).
It clears when the next text streams, when the reply ends, on an error, and once a run is stopped and the reply is over; no event is left to outlive the stream.
The label is announced once through the status announcer, and the elapsed time (after 5 seconds) never is.

The server half is the `tool_call` event, `{"event": "tool_call", "name": "..."}`, sent once per client-tool or server-tool call from each streaming loop as the model begins writing it.
There is no second event for it (the `tool_composing` proposed in issue #494 would only have duplicated `tool_call`):
a widget that predates it labels from `tool_start` and `tool_end`, and a server that predates it simply shows no "Writing code...".
`tests/test_api/test_tool_call_streaming.py` drives the real streaming functions with chunked calls, and `frontend/test-widget-activity-status.js` and `frontend/browser-harness/activity-status-check.mjs` hold the widget side.

### Taking a chat's code out: the notebook card and Download code

A community whose runtime has loaded (a `runtime` in its config, `docs/community-browser-runtime.md`) gives a chat's code three ways out (issue #675).
All three build from the chat's stored runs, in message order and without the runs that only read an output back (`get_full_output`), with the runtime bundle's `OSARuntime.buildConversationNotebook`: the same notebook and percent-format script for each, and the same count of steps (the runs that succeeded and have code; one that failed, was denied, timed out, was cancelled or ran out of memory is listed in the notebook's closing note instead).
A community with no runtime has none of them, and a chat with no dataset context (an empty context, or the dataset search) has none, since the files are named from the dataset.

- **The card.** Under the last reply, once it is whole, a bordered note appears after the first successful run, in the notice colors with secondary-style buttons: "N steps ran in this chat. Keep going in a notebook with all of their code." It is drawn again when the runtime bundle loads late and when the page's dataset changes.
  It is not drawn while a reply is loading, and it is not drawn for a chat whose runs all failed or were denied, or whose builder throws or finds no step.
- **Continue in notebook** is on the card only when the widget is a capsule, the page's dataset has a Zarr copy (`setDataset`'s `zarr: true`), and it is the chat's dataset; otherwise the card offers only Download code, and says to download.
  Clicking it builds the notebook and calls `importNotebookToTab` (above, "The notebook tab"), which opens the notebook tab, writes `<community>/<dataset> · <chat title>.ipynb`, and offers Run all and Undo in the banner.
  The button is disabled while it waits; a refusal or an error (including a notebook that does not answer) is said on the card with `role="alert"`.
  After an import the button reads **Open notebook**, which switches to the notebook tab and the imported notebook and does not import again, until a new successful run changes the step count (then Continue offers the chat's new code) or the import is undone.
  This memory is in the page only: after a reload the card offers Continue again.
- **Download code** opens a small menu, **Notebook (.ipynb)** and **Python script with cells (.py)**, with "Opens as cells in VS Code, Spyder and Jupytext."
  Each saves one file, named for the chat's dataset (`on002718-chat.ipynb`, `on002718-chat.py`), holding exactly what the builder returned (the notebook as JSON with one-space indentation; the script as text).
  Escape closes the menu and returns focus to the button, the arrow keys move between the items, and moving focus out of the card closes it.
- **The History row menu** has the same two items, **Continue in notebook** and **Download code** (which unfolds the two files), for a chat whose context is a dataset and that has a successful run.
  Continue is disabled, with its reason in text under it, unless that dataset is the page's and its notebook is available; for another chat than the open one it imports that chat's runs, closes the sheet, and leaves the open chat as it is.

Everything from the conversation (the title, the descriptions, the code) reaches the page as text, never markup, and the notebook and the script are built from the stored run records, not from the page.

Testing: `frontend/test-widget-notebook-entry.js` runs the real widget and the real runtime bundle in happy-dom over conversations from `frontend/test-support/notebook-conversations.js`, with files recorded on the way to their link click; it runs in CI.

## Quick answers and analysis: the `handover` events

A community that sets `quick_model` and `analysis_model` (NEMAR: `haiku` and `sonnet`; both or neither, each an offered model, and `execute_code` configured) answers most turns on the quick model and runs code, plots and analysis on the analysis model (issues #670 and #674, plan E8).
This is the stream contract the widget's handover card reads (issue #677, plan E8.3, board A6, described under "The widget" below); a widget that predates the card ignores the events and keeps working, as below.

**When a turn is routed.**
Routing applies to a streamed `/chat` or `/chat/resume` request that declares `execute_code` in `client_tools` and names no `model`.
A reader's own model choice always wins, and a request that declares no `execute_code` runs on the community's `default_model`, as it always did.
Every other community, and every non-streamed request, behaves as before.

**Which model a turn uses.**
A turn is analysis when its session is already in analysis (a session never goes back), or when the message asks for one in so many words (`run this`, `run it`, `run that`, `run the code`, `plot`, `analyze`, `compute`, `execute`, `power spectrum`, `spectra`, `ERP image` or `filter the data`, as whole words, in any case), or when the quick model calls its `start_analysis` tool.
Otherwise it is quick.
The words are narrow on purpose: a session never goes back to quick, so a false match would move a reader to the analysis model for the rest of the conversation. Everyday NEMAR and BIDS words (`runs`, `run-01`, `spectrum`, `filter`, `fit`) do not match, and a request the words miss is caught by `start_analysis`.
The quick model has no `execute_code` and no `get_full_output`; it has `start_analysis`, and its prompt says to call that for a request to run, plot or analyze instead of writing a recipe.
`/chat/resume` always runs on its session's tier.

**Why a new session.**
Switching models inside one session would send the quick model's thinking blocks to the analysis model, which its checks reject (issue #522).
So the conversation is compacted into a new session, and the old one is left as it was: valid, on the quick model, with no tool call left waiting.

**The events.**
When the session has earlier turns, three `handover` events precede the analysis run, in this order:

```text
data: {"event": "handover", "stage": "summarizing"}
data: {"event": "handover", "stage": "summarized", "messages": 4, "words": 90, "summary": "The reader searched for ..."}
data: {"event": "handover", "stage": "started", "session_id": "<new id>", "model": "claude-sonnet-5-5"}
```

- `summarizing` means the quick model is writing the summary.
- `summarized` carries the summary, the number of earlier messages it stands for (`messages`) and its length in words (`words`).
  The summary is at most 150 words: the quick model is asked for that, and the server cuts it to that, whole words only.
  It is built from the reader's and the assistant's text alone, with no tool output or image, and keeps the dataset ids the reader chose and their goal.
  It is what the analysis model is told, with the reader's message; the card's "What the analysis model is told" shows it.
  The analysis model is told it is background context, not instructions, and is given it apart from the reader's message.
  If the quick model fails (after one retry) or writes nothing, the handover still happens: the summary is the tail of the reader's own last three messages, and `summarized` carries `"degraded": true`.
- `started` names the new session and the analysis model.

The analysis turn then streams as any turn does, on the new session, so its own `session` event and `done.session_id` carry the new id.
A widget adopts that id from `session` and `done`, as it already does, and an older widget ignores the `handover` events.

The quick model's call to `start_analysis` is not sent as a `tool_call` event (there is no tool for a client to show).
Its prompt tells it to write nothing else, so a reply that hands over normally has no quick-model text before the handover.
`frontend/test-widget-handover-events.js` runs the widget against this stream: each `handover` event moves the card on one step (and ones it does not know change nothing), the second `session` event is adopted, and the next message goes to the new session.

A first message has no earlier turns, so there is nothing to summarize: its own session is flipped to analysis, with no `handover` events.

**Metrics.**
A handover logs two rows in `request_log`: the analysis run's under the request id `done.request_id` carries (what feedback joins on), and the quick model's work under `<request id>:quick`, which is the quick run that called `start_analysis` (if any) and the summary, priced at the quick model.
The summary's tokens are counted, and it is traced with the request in LangFuse.
Every row a chat request writes also carries `tier` (`quick` or `analysis` for a routed request, empty otherwise, and always empty for a request that is not streamed, which is never routed) and `conversation_id` (a non-streamed `/chat` carries its session's too; `/ask` has none).
A conversation's id is its first session's id, and the analysis session a handover opens inherits it, so both models' rows, and every later turn on the new session (including `/chat/resume`), group as one conversation.
An analysis session that expired, or was lost when the server restarted, is recreated by the next request with its own id as the conversation id, so the report shows it as a new conversation.
A streamed request writes one row, its generator's, under the request id: the metrics middleware no longer adds a stub row (no model, no tokens) beside it (#687), and when the reader disconnects before the reply ends, the generator still writes the row, with status 499 and the error message `client disconnected` (and the tokens counted so far; `500` and the exception's type when something raised past the stream's own handlers).
A request that is refused because the session is already working on a reply is a row with status 409.
Rows written before this change may also have a stub, which the cost report ignores, and rows written before the two columns existed have them empty.
`osa metrics cost-report` prices them, as below.

**`done.mode`.**
On a routed request, `done` carries `mode` (`"quick"` or `"analysis"`) beside `model`.
It is absent on a request that is not routed.

### The widget

The widget draws the handover from these events, in the layout of board A6 (`.context/designs/nemar-ai-ux/WidgetHandoff.dc.html`).
It is tested on a real DOM by `frontend/test-widget-handover-card.js`.

- **The card.**
  `summarizing` opens a card, "Starting an analysis session", with a live step, "Summarizing our conversation…".
  `summarized` turns it into "Summarized our conversation (N messages, W words)" and adds a native `<details>`, "What the analysis model is told", closed, holding the summary.
  When the summary is `degraded` the step says the summary could not be written and the analysis model is told the reader's own words, and the details hold those words.
  `started` adds "Handing it to the analysis model with your question" (live until the reply has text) and a `role="separator"` divider, "Analysis session · Claude Sonnet 5.5", where the label comes from the widget's model list and an id it does not know is shown as given.
  The card sits before the analysis reply it belongs to, and is drawn while that reply is still behind the loading bubble.
  A stage or a field the widget does not know is ignored.
- **The summary is text.**
  It is model output, so it is only ever set as text (never as markup), bounded to 2000 characters, and a summary that holds HTML stays inert.
- **The mode.**
  `done.mode` is kept on the reply.
  While the latest reply reports a mode, the header shows a pill (`Quick` or `Analysis`, `aria-label` "Mode: …") and a footnote above the input reads "Quick answers use a smaller, faster model. Running code and plots use the analysis model."
  A reply that ran in quick mode carries a "Quick answers" tag beside its label.
  A community whose stream sends no `mode` shows none of this, and a first message that flips its own session shows the label and no card.
- **Stored chats.**
  The handover (`stage`, `messages`, `words`, `summary`, `degraded`, `model`) and the mode are stored on the reply, normalized and bounded on the way back in (`normalizePersistedMessage`), so a reload, or a chat opened from History, draws the same card (details closed), divider and label.
  Starting a new chat clears the label and the footnote, since they follow the latest reply of the open chat.

## Cost per conversation: `osa metrics cost-report`

```text
osa metrics cost-report --community nemar --since 7d [--db PATH] [--json]
```

Prices each conversation of a community, the quick model against the analysis model (issue #678, plan E8.4), from the `request_log` the server already writes.
`--since` is an age (`7d`, `12h`, `2w`) or a date, optionally with a UTC offset (`2026-11-01T00:00-08:00`; it is converted to UTC, and a date with no offset is UTC).
`--db` defaults to this machine's own metrics database (the command never creates its directory), and a copy pulled from a server works.
The database is opened read-only (SQLite `mode=ro`), and one from before `tier` and `conversation_id` existed is read without being migrated.

**Getting a copy of a server's database.**
The server's database is in WAL mode, and rows that are not yet checkpointed live in `metrics.db-wal`, so copying `metrics.db` alone can leave recent rows out.
Take a consistent snapshot on the server instead (the container has no `sqlite3` command, so use Python; `VACUUM INTO` works on a read-only connection and writes a file that is not in WAL mode):

```bash
ssh -o "RequestTTY=no" -J hallu hedtools "docker exec osa-dev python3 -c 'import sqlite3; sqlite3.connect(\"file:/app/data/metrics.db?mode=ro\", uri=True).execute(\"VACUUM INTO ?\", (\"/tmp/metrics-copy.db\",))'"
ssh -o "RequestTTY=no" -J hallu hedtools "docker cp osa-dev:/tmp/metrics-copy.db /tmp/metrics-copy.db"
scp -J hallu hedtools:/tmp/metrics-copy.db ./metrics-copy.db
osa metrics cost-report --community nemar --since 7d --db ./metrics-copy.db
```

(`VACUUM INTO` fails if the target file exists; remove `/tmp/metrics-copy.db` in the container before a second snapshot.)
Alternatively, copy `metrics.db` together with `metrics.db-wal` and `metrics.db-shm` into one directory.
When the file it is given is in WAL mode with no `-wal` beside it, the command opens it as an immutable snapshot (which creates no `-wal` or `-shm` file beside the copy, so the warning repeats on every run) and prints a note to stderr, and adds a `note` field to `--json`, saying that rows still in a `-wal` file that was not copied are not included; a `VACUUM INTO` snapshot never triggers it.
A symlinked database is judged by the `-wal` beside the real file.
If SQLite cannot open a file read-only although its `-wal` is there (for example a read-only directory, where no `-shm` can be created), the command falls back to an immutable snapshot too and says, in a second note, that the rows in the `-wal` file are not included.
Only an "unable to open" failure makes it fall back; a locked database is an error.

The table has one row per conversation: its short id, start, turns by tier (`quick/analysis`), the quick cost, the analysis cost and the total, followed by totals by tier.
A turn here is one model's work on one request, so a handover counts once on each side.
A request that failed (an error status or an error message, which includes a reader's disconnect, status 499) is not a turn: it is counted under `errors` (a "Failed" column in the totals when there are any), so retries after a failure do not inflate the turns or the quick/analysis ratio; any cost it did log is still added.
Cost is each row's own estimate (`estimate_cost`, at the rates of the time it was logged).
Requests that belong to no conversation (everything logged before this change, and `/ask`) are listed apart, by model, and the middleware's stub rows (no model) are left out.
`--json` prints the same report as JSON.

## Plan first: `plan_first_instructions` and `propose_plan`

A community that routes between two models can make the analysis model show the reader a plan before it runs code (issues #660 and #661, plan E2).
The widget answers the call with an editable plan card and shows the plan's progress (E2.3 and E2.4); see [The plan card and step progress](#the-plan-card-and-step-progress) below.
It declares `propose_plan` in `client_tools` on `/chat` and `/chat/resume` once the runtime bundle (which carries `OSARuntime.plan`) has loaded, for every community that configures `execute_code`; the server binds the tool only on an analysis turn, so a community with no `plan_first_instructions` never sees a plan.

**The key.**
`plan_first_instructions` is a string of prompt text, valid only with `quick_model` and `analysis_model` and not blank.
The server appends it to the system prompt of an analysis turn, and only when the request declares `propose_plan` in `client_tools` and client tools are not disabled (`OSA_CLIENT_TOOLS_DISABLED`).
A quick turn, a request that does not declare the tool and a community that sets no text get none, so the prompt never names a tool the model was not given.
NEMAR's text (`src/assistants/nemar/config.yaml`) applies to requests to analyze, plot or compute: inspect with `nemar_describe_dataset`, `nemar_get_events` and `nemar_list_recordings`, report what was found, call `propose_plan` with 3 to 6 steps, then run exactly one `execute_code` per step.
A plain question gets a direct answer and no plan.

**The tool.**
`propose_plan` is derived like `get_full_output`: a community never configures it (`propose_plan` is in `RESERVED_CLIENT_TOOL_NAMES`), and the server binds it only on an analysis turn, beside a python tool, for a request that declares it.
It has no permission gate and runs nothing.
Its arguments:

| Field | Bounds |
|-------|--------|
| `findings` | text, at most 2000 characters |
| `steps` | 1 to 8 of `{title, detail}`; `title` 1 to 120 characters, `detail` at most 600 |
| `est_peak_mb` | integer, 0 to 8192; validated and recorded only, the memory budget is separate work (E5.1) |

A call that breaks a bound is answered with an error `ToolMessage`, as for any client tool, and the model calls again.
A valid call parks the graph and sends a `tool_request` with `tool: "propose_plan"`, `requires_permission: false` and these arguments, like an `execute_code` call.

**The answer.**
`POST /{community}/chat/resume` takes a `PlanDecision` as `result` for a plan call, in place of the run result:

```json
{
  "session_id": "...",
  "client_tools": ["execute_code", "get_full_output", "propose_plan"],
  "result": {
    "call_id": "toolu_...",
    "decision": "approved",
    "steps": [{"title": "Load epochs", "detail": "Load runs 1 and 2."}],
    "run_mode": "pause",
    "feedback": ""
  }
}
```

- `decision` is `approved` or `changes_requested`.
- `steps` are the steps as the reader left them after edits, adds and removes, 1 to 8 of them.
- `run_mode` is `pause` (the default; stop after each step) or `run_all`.
- `feedback` is at most 1000 characters, for a request for changes.

The server turns the decision into the tool message the model reads: the decision, the run mode, the numbered steps and whether they differ from the proposal (worked out on the server from the parked call, never taken from the client), the feedback, and for changes "revise the plan and call propose_plan again; run no code".
The kind of answer must match the parked call: a run result for a plan, or a decision for a code call, is a 409 and the call stays parked.
Answering a plan does not use any of the `MAX_BROWSER_RUNS_PER_REPLY` runs a reply may make, and a plan is not a run of code for the widget's keep-the-reply rule.

### The plan card and step progress

**The card** (`frontend/osa-chat-widget.js`, the Plan card block; the logic is `frontend/osa-plan.js`, exposed as `OSARuntime.plan`) is the answer to a `propose_plan` request.
It shows what the assistant found, `Proposed plan · N steps`, one line of memory (`Largest step needs about N MB`, with no budget named, which comes with E5.1) and the steps.
Each step has Edit (a title field of at most 120 characters and a detail field of at most 600, each with a label), and Remove, which stops at one step; `+ Add a step` stops at eight.
A title is kept to one line, as the server keeps it.
Two radios choose what happens when a step finishes: **Pause so I can look** (the default, `pause`) or **Run all steps** (`run_all`).
**Approve and run step 1** posts the decision with the steps as they stand; **Ask for changes** opens an optional note of at most 1000 characters and posts `changes_requested`, which runs no code.
Approve, Ask for changes, the Edit and Remove buttons and the bar's buttons are at least 44 px tall.
Limits count code points, as the server's do, so a title with emoji is cut at a character, never in the middle of one.
While a step is being edited the decision waits, so an unsaved edit is never sent or lost.
The body is exactly `{session_id, result: {call_id, decision, steps, run_mode, feedback?}, client_tools}` (and `page_context` when there is anything to send: the shared page, the dataset, the memory budget); `feedback` is left out when it is empty.
The model's findings, titles and details, and the reader's text, are escaped.
While the card waits, the input is off and the conversation says it is waiting for the decision; a plan the reader never answers leaves the parked call to the server's 15-minute limit.

**Approval is consent.**
After an approval, a run of `execute_code` that is the plan's next step, or the second attempt of a step that failed, is not asked about; the widget answers the controller's permission gate with Run, so `frontend/osa-controller.js` is unchanged.
Any other request asks as before: a third attempt at one step, a request after the last step, any request after **Stop plan** or **Ask for changes**, and any code in a plan that is not approved.

**The trust model: consent is by position, not by content.**
The widget cannot tell whether a request's code is the step the plan describes, so it does not try: once a plan is approved, the next `execute_code` request after the plan, or after an `ok` run, is the next step, in this reply or in any later one.
In pause mode that includes a request the model makes in answer to an unrelated question typed between steps; it runs without a permission card.
What approving allows is bounded by the sandbox (Python in the reader's browser, the egress allowlist, the limits on a run), by the plan's own length (a step runs at most twice, and nothing runs after the last step), and by the reader, who can press **Stop plan** at any time and who reads each step's code on its run card.
A new plan, from "Change step N", must be approved again, and counts only the runs from the reply that proposed it onward.
A decision that does not reach the server (the post fails, or the parked call has expired) is undone on the page: the plan reads `Not answered`, is read-only and covers no run, so its code asks as before.

**Which step a run is** is decided in the widget, with no change to the server.
The first run of an approved plan is step 1; a run after an `ok` run is the next step; a run after a run that did not work (`error`, `timeout`, `oom`, `denied`, or one the reader stopped) is the next attempt of the same step, to two attempts.
`denied` under an approved plan is the runtime's refusal, not the reader's (the gate answers Run), most often a package the runtime does not have (`denied_import: scipy`), so it is a failed step like an error.
A run that is none of these is not a step.
Each stored run carries `step`, `attempt`, `stepTitle` and `elapsedMs`, so the run card says `Step 2 · <title>` (or `Step 2 failed · KeyError: 'x'`, `Step 2 failed · denied_import: scipy`, or `Step 2 stopped`) with how long it took, a failed step's retry line says which attempt is next, and `OSARuntime.buildConversationNotebook` names the notebook's step by the title.

**The strip and the bar.**
A strip at the top of the conversation says `Plan · step N of M`, with a segment for each step and `Pauses after each step` or `Runs all steps`.
A bar at the bottom says what the plan is waiting for:

| State | Bar |
|-------|-----|
| The reply is running | `Working on step N of M.` and **Stop plan** |
| A step failed and has an attempt left, and the reply goes on | `Step N+1 waits until step N works.` and **Stop plan** |
| Paused after a step | **Run step N** (sends the message `Continue with step N.`), **Change step N** (focuses the input with `Change step N: ` typed in; the model must propose a new plan) and **Stop plan** |
| A step has failed with no attempt left | **Change step N** and **Stop plan** |
| Done or stopped | none; the strip says `Plan · all M steps done` or `Plan stopped · step N of M` |

A polite live region of its own, outside the conversation, says where the plan stands (`Plan · step 2 of 3. Paused after step 1.`) each time that changes, since the strip and the bar are redrawn with the conversation.
After Approve, focus moves to the card's heading; after **Run step N**, to the strip (or, in the center-stage dialog while the rail hides the strip, to the rail, then the thread).

**Stop plan** marks the plan stopped, cancels the run in progress, and the reply answers no further request for code: the request that arrives is left parked, as one past the 20-run cap is, and the next message repairs it.
A plan is the newest plan in the conversation, so a paused plan continues in the next reply, and a plan proposed after "Change step N" replaces it.

**A reload** keeps the plan on its reply (its steps as approved, its run mode, its status) and each run's step fields, bounded.
The restored card is read-only and says `Approved` (or `Changes requested`, `Stopped`, or `Not answered` for a plan nobody answered); the strip and the bar come back once the runtime bundle has loaded.

**The plan rail.**
In the center-stage dialog (see [The host API and the center-stage dialog](#the-host-api-and-the-center-stage-dialog)) the same plan state also drives a rail beside the conversation, board C3; the corner panel has none.
The logic is `frontend/osa-plan-rail.js`, exposed as `OSARuntime.planRail.planRailOf(messages, {active})`, which reads `currentPlan` and `progressOf` from `osa-plan.js` and adds no progress model of its own; it returns `null` or `{heading, doneCount, total, mode, modeLabel, rows: [{n, title, state, hasCard, label}]}`.
The rail shows for a proposed plan (`Proposed plan`, every step pending), an approved one (`Plan · 3 of 5 done`), a stopped one (`Plan stopped · 3 of 5 done`) and a finished one, and not for no plan, one the reader asked to change, or one nobody answered.
A row is `done`, `current` (the step the plan is on or waiting to run, highlighted, `aria-current="step"`), `failed`, `stopped` or `pending`; the icon differs by state (a check, a ringed circle, an empty circle, a circled mark, a circled bar), and every row says its state in words for a screen reader (`Step 3, done`), so color is never the only cue.
A row whose step has a run card is a button that scrolls to and focuses the newest card of that step (the card has `data-plan-step`); the others are plain text; if a card is not in the thread, the click goes to the newest card, or the thread, so it always does something.
It uses buttons, not `#` links, so the host page's address and history never change.
The foot shows the run mode, read-only (the mode goes to the model once, in the decision, and there is no way to change it afterwards), and `Memory budget 512 MB · change`, a button that opens Settings with focus on the memory choice that is on.
While the rail shows, the progress strip in the thread is hidden (the rail says the same) and the paused bar stays, with **Run step N** and **Change step N**.
At 900 px and under the rail is not drawn and the strip carries the plan, as in the corner.
It is 280 px wide, in the panel's own colors, and the window (or, for a capsule community, the chat view in it) is a grid only while it has a rail: the header across the top, the rail down the left, the thread and its input on the right, the disclaimer and footer across both.
The rail is inserted before the thread and removed again on docking; the thread's nodes are never redrawn or moved, and Tab runs header, rail, thread, input.

Testing: `frontend/test-plan-rail.js` (the rail's logic) and `frontend/test-widget-plan-rail.js` (the rail and the header's Continue in notebook, on a real DOM) cover the rail; `frontend/test-widget-plan-card.js` runs the real widget and the runtime bundle as built in happy-dom, with `fetch` answering recorded-shape SSE; `tests/test_frontend/test_plan_resume_payload.py` feeds the payload the widget really posts to the server's `ResumeRequest`, `PlanDecision` and `build_plan_tool_message`.

## Browser memory: the budget and the heap report

A browser tab has a fixed amount of memory to give the Python runtime, and a laptop may have little to spare.
A community with a browser runtime therefore lets the reader say how much one step of an analysis may use (E5.1, board A5), and tells them how much each step did use.

**The setting.**
Settings has a **Browser memory** field with four 44 px choices: 256 MB, 512 MB (the default), 1 GB and 2 GB.
It appears only on a page that can run code in the browser, after the model and key fields.
Choosing is part of the dialog like the model: **Save** stores it, **Cancel** leaves what was saved.
The choice is stored with the other user settings (`memoryBudgetMb` in `osa-settings-<community>`).
A stored value that is not one of the four, such as a hand-edited `300`, reads as 512 and is never sent.
Below the choices, **Runtime memory after the last step** shows the heap of the latest run in the chat against the chosen budget, in MB, with a bar in the caution color when it is over (and "over budget" in the text for a screen reader, since the bar is decorative).

**The request.**
The widget sends `page_context.memory_budget_mb` on `/chat` and `/chat/resume`, whether or not the reader shares the page URL, and only from a page whose browser runtime has loaded and that declares its tools on the request (a first request sent with streaming off declares none, so it states none).
A page that cannot run code sends nothing, and `page_context` stays absent when nothing else is set, as before.
The rest of the first request's page context (the URL, the dataset, a carried move) is read when the question is sent, not after the wait for the runtime, so a reader who navigates during that wait still asked about the page they were on.
The server accepts exactly 256, 512, 1024 or 2048 and refuses any other value with a 422 (`PageContext` in `src/api/routers/community.py`); the agent's own `PageContext` dataclass raises `ValueError` for any other value too, since it is what the prompt is written from.
The system prompt then carries its own paragraph, `## Browser memory budget`: "Each step must fit in B MB of the reader's browser memory; read long recordings in pieces."
A community that sets `enable_page_context: false` states none.
NEMAR's `plan_first_instructions` also require the plan's `est_peak_mb` not to exceed it.
The budget is advice to the model, not a limit: nothing enforces it, on the server or in the tab.

**The heap report.**
After each run the worker reads the size of Pyodide's WebAssembly memory (`pyodide._module.HEAPU8.byteLength`) and sends it beside the result as `heap_bytes`.
It is not a `ClientToolResult` field and never reaches the server: `PyodideRuntime#heapBytes(callId)` hands it to the widget, which stores it on the run record as `heapMb`, with `budgetMb`, the budget the run was under.
WebAssembly memory only grows, so the figure is the most the tab has had to give the runtime so far, the interpreter and its packages included (about 20 MB before any package loads), not what the one step held.
The run card says `Heap N MB of B MB` under the figures, with the same MB wording in every line (Settings' four choices alone read "1 GB" and "2 GB").
Because the figure only grows, only the run that took it past the budget is blamed.
That run's line takes a warning style and a second line names the step: "Step 2, Epoch the EEG, used more than the 512 MB budget. Ask for smaller pieces, or raise the budget in Settings." (a run no plan covers is "This run"), and so does a later run that grows an over-budget heap further.
A run that reports the same figure as the run before it, `print(1)` after a large read included, says "The runtime is still holding N MB from earlier steps; its memory does not shrink between runs." as a plain note, not in the amber box, and keeps the plain heap line.
The widget decides this from the heap the runtime reported before the run, in whichever chat that was (the runtime is shared by every chat on the page, so a new chat or a conversation opened from History starts on what an earlier one left), which the run record keeps as `heapBeforeMb`; a smaller figure than that one belongs to a runtime started since (a reload, a restart after an out-of-memory crash), so its first over-budget run is blamed.
A reload and the History sheet restore the line, against the budget the run had, whatever the setting is now.
A run with no figure (one refused before it started, a timeout) has no line.

**The plan card** keeps "Largest step needs about N MB" and, while the plan is a draft, adds a warning when N is more than the budget.

The pure logic is `frontend/osa-memory.js`, exported as `OSARuntime.memory`.
The widget holds its own copy of the four sizes and the heap limit, because a saved setting is read before the runtime bundle has loaded, and the heap line itself waits for the bundle (a chat restored before it loads is drawn again when it does).
`frontend/test-widget-memory-budget.js` holds the copies to each other and to the server's list.

The caution color is one token, `--osa-warn` (`#b45309` on the light panel, 4.9:1; `#fbbf24` on the dark one), which the Settings bar, the run card and the plan card read.

Testing: `frontend/test-widget-memory-budget.js` runs the real widget and the runtime bundle as built in happy-dom, with a test worker that reports a heap when the code says `HEAP_MB=<n>`; `frontend/test-worker-core.js` holds the real worker's figure against real Pyodide; `tests/test_api/test_page_context.py` and `tests/test_agents/test_page_context.py` hold the server's side.

## Saved conversations

The widget keeps up to 30 conversations in the reader's browser, in one `localStorage` record per community and origin (`osa-chat-history-<community>` unless `storageKey` says otherwise).
The record is `{version: 3, activeId, conversations: [...]}`.
Each conversation has an id, a title (its first question, cut to 60 characters), a context (none, a dataset, or the search page), the server's session id, its two timestamps and its messages.
When a 31st is saved, the one that last changed longest ago is dropped; the active conversation is never dropped.
A conversation changes when a message is added or edited, a run is edited, or its session changes; a thumbs-up or thumbs-down does not count.
A conversation with no question from the reader yet (only the greeting) does not use one of the 30 places, so starting a new chat never costs the oldest saved one.

- **The widget shows the active conversation**, as it always showed its one chat, except on [a fresh chat on return](#a-fresh-chat-on-return). The History panel is built on this store.
- **Older records are migrated on load.** A record any earlier widget wrote (version 2, version 1, or a bare list of messages) becomes one conversation, with its messages and session kept.
- **A second tab, or the pop-out, does not drop the first's conversations.** Each save re-reads the record and replaces only its own conversation.
- **A record from a newer widget is left alone.** If the stored version is above 3, the widget says so, does not save, and does not overwrite it.
- **A corrupted record is reported, and a copy is kept before it is replaced.** If the stored text is not valid JSON, or is a version 3 record without a conversations list, the widget says it is corrupted and shows an empty chat; loading changes nothing.
  The first save after that (the reader's next message) copies the unreadable text, byte for byte, to `<storageKey>-unreadable`, then writes a fresh record.
  If the browser storage is too full to hold the copy, the record is replaced anyway and the reader sees a notice that no copy was kept, because refusing to replace a record the widget cannot use would stop every later save.
  If the copy fails for any other reason, nothing is overwritten and the save fails with the usual storage message.
  The copy is never cleaned up by the widget, and a second corruption replaces the first copy; remove the `-unreadable` key by hand to free the space.
- **A full browser storage drops the oldest other conversation** and tries again, before it reports that the chat could not be saved.
  When that retry succeeds, the reader sees a notice that the oldest saved conversation was removed: at once, and until dismissed, if the panel is open; otherwise the next time it opens.
- **Mixed widget versions share a record.** The record keeps the same `storageKey` as before, so a version 2 widget (a cached or integrity-pinned embed, or another page on the same origin) that reads a version 3 record finds no top-level `messages`, shows the greeting, and its first save overwrites the whole record with a version 2 one.
  Only a widget from the future is guarded against (the version above 3 rule); an older one is not.
  Embeds that pin the widget by its integrity hash should be updated together, and a release note says so.
- **Clearing the chat** (the header's reset button) discards the active conversation and starts an empty one; the others stay.

Testing: `frontend/test-widget-history-store.js` runs the real widget source in happy-dom windows: the migrations, the 30-conversation limit, the save and load round trip, the records it must not overwrite, a full storage, and two widgets on one storage.

### A fresh chat on return

A reader who comes back to the page starts a new chat; the one they left stays saved.
When the widget loads and the active conversation holds a question from the reader, it starts a new conversation if either of these is true:

- **A new browser session.** The first load in a tab sets a marker, `<storageKey>-session`, in `sessionStorage` (so a reload or a second page of the same tab keeps its chat, and a new tab or a restarted browser does not).
- **The chat is older than the server's session.** Its last change was more than 24 hours ago, the same `SESSION_TTL_HOURS` after which the server forgets a session (`tests/test_frontend/test_widget_drift.py` keeps the two equal).

A conversation with no question from the reader is never replaced.
A browser that refuses `sessionStorage` reads as "the marker is there", so such a reader is not reset on every load; the 24 hour rule still applies.
The [pop-out](#the-pop-out-window) never does this: it has a `sessionStorage` of its own and must show the same chat as its opener.

The new chat takes the page's context (see [a context change](#a-context-change-starts-a-new-chat)) and its empty state says so: "Welcome back. This is a new chat about `<dataset id>`." and, under it, a card, "Pick up where you left off: `<title>`".
Clicking the card makes the conversation just left the active one again, with its messages and server session (a session the server has already forgotten is started again from the chat itself, see [a conversation the server forgot](#a-conversation-the-server-forgot)); the card is hidden after the first message.
The card is not stored: it is shown only on the load that started the new chat.

Testing: `frontend/test-widget-fresh-return.js`.

### A conversation the server forgot

The server holds a chat's session for 24 hours after its last request, and loses it on a restart or when it is the least recently used of a community's 1000.
The widget cannot tell, and a server that is sent a session id it does not know makes an empty session under that id, so a resumed chat used to be answered with no context.
Now a request carries the chat so far when the widget cannot know the server has it, and the server uses it only when the request just created the session.
One case is not covered: once the server has confirmed the session in a page load, the widget sends nothing for up to 24 hours, so a server restart or an eviction while the page stays open still starts an empty session under the old id (tracked in issue #698).

- **When the widget sends it.**
  Until the server has confirmed the session in this page load (a `session` or `done` event, or a non-streaming reply, with the id), every `/chat` request carries `prior_turns`, and `mode` when a reply said one.
  So the first request after a page load, after a chat is opened from History or the resume card, and after a request the server never answered, carries it; a conversation that has had no server answer for longer than `SESSION_TTL_HOURS` carries it again.
  Nothing is sent for a new, empty chat or for one with no assistant reply, and nothing once the server has confirmed the session; a restart or eviction after that, with the page still open, is not detected.
  The confirmation is not stored.
  The pop-out is a page load of its own, so its first request carries the chat too.
- **What it carries.**
  `prior_turns` is `[{role: "user" | "assistant", text}]`, oldest first, the conversation before the message being sent: the text of the reader's messages and the assistant's replies, never a code run, tool result, image or figure, and never the greeting.
  The newest 12 turns that fit 12,000 characters in all (the most the server keeps), whole oldest turns left out first, each turn cut from the middle to 2,000 characters with an ellipsis.
  `tests/test_frontend/test_reseed_limits.py` keeps these limits within the server's.
  `mode` is `quick` or `analysis`, taken from the newest reply that carries one (stored by the [handover](#quick-answers-and-analysis-the-handover-events) work), so the tier survives.
- **What the server does.**
  `POST /{community}/chat` accepts `prior_turns` (at most 24 turns of at most 4,000 characters, a role of `user` or `assistant`, no blank text, no other field; anything else is a 422) and `mode`.
  When the session this request names is new (it has no messages) the server builds one message from the transcript, whole oldest turns dropped to fit 12,000 characters (it then opens with `[earlier turns omitted]`), and the reader's message.
  The transcript is framed as background, in the reader's voice, and fenced in `<earlier_conversation>`; the message goes in `<researcher_message>`.
  The framing says the browser restored the conversation automatically and asks the assistant to continue it as its own, without mentioning a transcript, a paste, a restore or a server.
  A fence written into a turn is removed so it cannot close its section.
  A session that already has messages ignores both fields.
  On a request that is routed between a quick and an analysis model, `mode` sets the new session's tier before the turn, so an analysis chat continues on the analysis model with no handover and no summary; elsewhere `mode` is ignored.
  A keyword in the transcript is not a request for analysis; only the reader's own message is read for one.
  If the reader later asks for analysis, the summary for the handover is written from the turns and the reader's message, not from the framing the server added.
- **What it does not do.**
  It does not store history on the server, summarize the transcript with a model, carry figures, or reseed `/chat/resume`, which still answers 404 for a session the server no longer holds.
  A request sent to a live session carries the transcript for nothing; the bytes are bounded (at most about 12,000 characters of text, before JSON escaping) and are the price of not asking the server first.

Testing: `frontend/test-widget-reseed.js`, `tests/test_api/test_reseed.py` and `tests/test_integration/test_reseed_llm.py`.

### A context change starts a new chat

A conversation has a context: the dataset the page names with [`setDataset`](#osachatwidgetsetdatasetvalue), or the dataset search, or none.
The page's context is:

- the dataset from `setDataset`, when there is one;
- otherwise the search context, when `location.pathname` is one of the community's `search_paths` or below one at a path-segment boundary (`/discover` matches `/discover` and `/discover/x`, not `/discovery`; `/` matches the home page alone, never every page; NEMAR sets `search_paths: [/discover]`);
- otherwise none, and the chat keeps the context it has.

The widget compares the two when it loads, whenever `setDataset` is called, when the community config arrives (it may name a search path), when the browser reports `popstate` (the back and forward buttons), and just before a message is sent (a single-page site can move without telling the widget).
A move made with `history.pushState` and no `setDataset` reports nothing, so it is found only at the send, and the new chat then starts without offering "Continue"; a single-page host that wants the notice calls `setDataset` (or `setDataset(null)`) on each navigation.

- **A chat with no question from the reader switches silently.** The decision: with nothing the reader has said, there is nothing the new context could be confused with, so the chat takes the page's context and no notice is shown.
  A chat that has questions but no context at all (one saved by an earlier widget) also takes the page's context silently.
- **Otherwise the old chat stays on screen** and a notice sits above the input: "You're on a new dataset, `<id>`. **Your next message starts a new chat;** the `<old>` chat is saved." (for the search page, "You're on the dataset search.").
  The next message starts a new conversation in the page's context; the old one stays saved with its messages and server session.
- **"Continue the `<old>` chat instead"** keeps the conversation and gives it the page's context.
  A divider ("Moved to `<id>` · continuing this chat") and a dashed note ("The assistant is told: the reader moved from `<old>` to `<id>`; compare or continue as they ask.") appear in the chat, and the next request carries `page_context.moved_from`.
  The move is told once: it is cleared when the server has answered a request that carried it (the first message or a resumed run), so a send that failed carries it again on the next try, and a reply to a request sent before "Continue" does not use it up.
  It is sent only while the page is still where the reader moved to; on a page that is neither that dataset nor the search page it waits.
  Two moves before a send are one, from where the chat started, and a move back to where it started is no move.
  The divider is for this page view and is not stored; the move is not stored either, so a reload before the first message after "Continue" drops it (the chat keeps the new context, and the assistant is not told about the move).
- **The pop-out never asks.** It shows its opener's chat and skips the comparison; its requests still name the dataset.

What the request carries (`page_context`, beside `url`, `title` and `widget_instructions`):

| Field | Value |
|---|---|
| `dataset_id` | The dataset `setDataset` named, matching `[A-Za-z0-9._-]{1,64}`. **Sent whether or not the reader shares the page URL** ("Use this page for answers" in [Privacy choices](#privacy-choices)): like `widget_instructions`, it is about the chat, not about the reader's browsing, so a reader who keeps the URL private still gets answers about the dataset on screen. |
| `moved_from` | `{type: 'dataset', id}` or `{type: 'search'}`: where the reader was before choosing to continue. Present only on the first request after "Continue". A `dataset` needs an `id`; `search` takes none, and the API refuses one. |
| `memory_budget_mb` | The reader's browser memory budget per step, one of 256, 512, 1024 or 2048 ("Browser memory" in Settings). **Sent whether or not the reader shares the page URL**, but only by a page whose browser runtime has loaded and that declares its client tools on the request; a page with no runtime sends none. |

The backend renders the dataset and the move in the system prompt's page context section, "Reader Context": the dataset the reader is on and, when `moved_from` is present, that the reader moved from one to the other in this conversation (a `moved_from` equal to where the reader is now is left out), and the budget as its own "Browser memory budget" paragraph.
A community with `enable_page_context: false` gets none of the three.

Testing: `frontend/test-widget-context-switch.js` (the widget), `tests/test_api/test_page_context.py` (the validated fields), `tests/test_agents/test_page_context.py` (the prompt), and the `search_paths` cases in `tests/test_core/test_config/test_community.py` and `tests/test_api/test_communities_endpoint.py`.

## Privacy choices

The footer has a **Privacy choices** button: nemar.org's opt-out symbol (30:14, 14 px tall, in its own fixed colors in both schemes, never themed) beside the text.
It replaces the footer checkbox "Share page URL to help answer questions", and opens a small modal dialog with two choices, both on by default, and a link to the privacy page.

| Choice | What it does | Stored in `localStorage` as |
|---|---|---|
| **Use this page for answers** | Sends the page's address and title as `page_context.url` and `page_context.title`. Off, neither is sent. The dataset a page names (`page_context.dataset_id`), the community's `widget_instructions` and the memory budget are about the chat, so they are still sent; the helper text under the choice says so. | `osa-page-context-enabled`, the key the footer checkbox used, so a reader's earlier choice carries over |
| **Share usage data** | Off, `POST /{community}/chat` and `POST /{community}/chat/resume` carry a top-level `share_usage: false`, and the server writes the request's usage row without its `conversation_id`, `tools_called` and `langfuse_trace_id`, and makes no Langfuse trace for the request. On, the field is left out. | `osa-share-usage-enabled` |

The dialog is another `.osa-settings-overlay` (also `.osa-privacy-overlay`) with `role="dialog"`, `aria-modal="true"` and `aria-labelledby`.
Each choice is a labelled checkbox in a 44 px row, with its helper text tied by `aria-describedby`.
Tab and Shift+Tab wrap inside it, in the corner as well as in the center dialog; Escape closes only it (the center dialog behind it stays, and a second Escape docks it), and focus goes back to the **Privacy choices** button.
A community whose embedder sets `allowPageContext: false` has no "Use this page" row; the button and the usage choice stay.

What `share_usage: false` changes on the server, and what it leaves:

- The request still runs, and its `request_log` row keeps the timestamp, community, endpoint, model, tokens, cost, tier, key source, status, duration, `tool_call_count` and `request_id`, so the community's budget and `osa metrics cost-report` still add up.
- The row has no `conversation_id` (so it cannot be grouped into a conversation), no `tools_called` and no Langfuse trace id.
- No Langfuse trace is made for the request (`create_community_assistant(trace=False)`), so no trace holds its messages and tool calls.
- Feedback (thumbs and free text) is still received and stored, but a thumb on an opted-out answer is not attached to a Langfuse trace as a score, because the answer has none.
- The server's in-memory conversation (the 24 hours that let a follow-up have its context), the search overview and the stored metrics already written are not changed by the choice.
- The widget re-reads the two saved choices when it builds a request, so a choice made in another tab, a pop-out or a notebook tab applies without a reload. When the browser will not save a choice, the dialog says so and the choice holds until the page reloads.

`POST /{community}/ask` and `/chat/resume` accept the same `share_usage` field (default `true`).
The Cloudflare worker forwards the JSON body unchanged, so the field needs no header or allowlist entry.

The link goes to `privacy_url` from the community's `widget` block, or, when that is unset or not valid, to `https://github.com/OpenScience-Collective/osa/blob/develop/PRIVACY.md`.
It is validated twice, as `analysis_docs` is: `WidgetConfig` refuses a value that is not an `https://` URL with a host, no credentials and no whitespace (at most 500 characters) when the config loads, and the widget checks again before drawing, falling back to the default.
It opens in a new tab (`target="_blank"`, `rel="noopener noreferrer"`, with a visually hidden "(opens in a new tab)").

```yaml
widget:
  privacy_url: https://nemar.org/privacy/
```

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

## The host API and the center-stage dialog

A host page can open the widget, ask it a question and move it, without the reader touching the launcher (E6.1, #701).
The calls are on `OSAChatWidget`, next to `setDataset`.

```js
OSAChatWidget.open({ mode: 'center' });          // the AI Mode dialog
OSAChatWidget.ask('What events are annotated?'); // open, then send as if typed
OSAChatWidget.setMode('corner');                 // dock the dialog
const stop = OSAChatWidget.on('modechange', ({ mode }) => console.log(mode));
stop();                                          // stop listening
OSAChatWidget.close();
```

| Call | What it does |
|---|---|
| `open({mode}?)` | Shows the window, in `mode` when one is given (`'corner'` or `'center'`), on the chat tab, with History and Settings closed. An open window stays open; the mode, if given, changes. |
| `close()` | Closes the window, as the Close button does. |
| `ask(text, {mode}?)` | Opens the window (in `mode` if given), then sends `text` through the same path as a typed question: it is in the saved conversation and History, the page-context reset of [A context change starts a new chat](#a-context-change-starts-a-new-chat) applies, and the browser runtime is preloaded as for any first message. `text` must be a non-empty string; a reply that is still loading refuses it. What the reader had typed in the chat's input and not sent is theirs: it stays there (and in the start screen's field) after `ask`. |
| `setMode(mode)` | `'center'` makes the window the dialog; `'corner'` docks it. On a closed window the mode is kept for the next open. The dialog is the chat's: a capsule community's window that is on the notebook tab is taken to the chat tab when it becomes the dialog, and the dialog docks to the corner (with a `modechange` event) when the notebook tab is opened from it, for example by the **Continue in notebook** or **Open notebook** button under a reply. |
| `on(event, handler)` | Listens for an event, below, and returns a function that stops listening. |

- **Before `init()`.**
  `open`, `close`, `ask` and `setMode` may be called before `OSAChatWidget.init()`, as `setDataset` may.
  They are kept and run, in the order they were made, once the widget is built, so a page need not wait for the script to be ready.
  `on` takes effect at once.
- **A bad call does nothing.**
  An unknown `mode`, an `event` that is not one of the four, a handler that is not a function, an `options` that is not an object, or an empty question each log a `console.warn` and change nothing.
  A bad `on` still returns a function, which does nothing, so a page that keeps and later calls it does not throw.
  A call checked before `init()` is refused at once, not queued.
- **Calls before `init()` are bounded.**
  At most 50 are kept; the next logs a `console.warn` and is dropped.
  If `init()` has not built the widget five seconds after the first call was kept, one `console.warn` says calls are waiting for it.
- **The mode is in memory only.**
  It is `'corner'` after a load, and a dialog that is closed is `'corner'` again, so the launcher always opens the panel it always has.
- **A pop-out has no corner.**
  In the pop-out window (`fullscreen`), `setMode`, `open` and `close` do nothing, each with a `console.warn`; `ask` still sends.

### Events

| Event | Payload | When |
|---|---|---|
| `open` | `{mode}` | The window opened, after the mode was applied. |
| `close` | `{}` | The window closed, by the Close button, the launcher or `close()`. |
| `modechange` | `{mode}` | The mode changed: `setMode`, `open({mode})`, Escape or Move to corner, or a dialog being closed (then `{mode: 'corner'}`, just before `close`). |
| `send` | `{text}` | A question was sent, typed or by `ask`; `text` is as the server receives it, trimmed. |

Each event fires once per occurrence.
A payload is a plain object, and each handler gets its own copy.
A handler that throws, or returns a rejected promise, is reported on the console and cannot stop the widget or the handlers after it.
Opening in a mode from a closed window fires `open` and then `modechange`.

### The dialog

Center is the same `.osa-chat-window`, with an `osa-center` class and a scrim behind it, never a second instance, so docking keeps the conversation: the message nodes, the session and what was typed are not touched.

- **Modal.**
  The window has `role="dialog"`, `aria-modal="true"` and the name `AI Mode for <dataset id>` (`AI Mode` with no dataset).
  The host page's other top-level elements and the launcher are `inert`, and the page does not scroll, until the dialog is closed or docked.
  Tab and Shift+Tab stay inside the dialog (or inside Settings or History when one is on top of it).
- **Escape.**
  It closes what is on top first: the History sheet, then Settings or the feedback panel, and then docks the dialog.
  The **Move to corner** button does the same as the last step.
- **Focus.**
  Focus moves into the dialog on open, and **Close** (or `close()`) gives it back to the element that had it before the window opened, or to the launcher when there was none.
- **Header.**
  The AI Mode pill, the dataset id and, when the page gave one, its title; History; **Continue in notebook** (below); Move to corner; Settings; and Close.
  `setDataset({id, title})` takes an optional `title`, a string of 1 to 200 characters.
  A title that is not one is dropped alone, with a `console.warn`, and the id is shown without it.
- **Continue in notebook.**
  A header button (`osa-center-notebook-btn`) that appears only in the dialog and only where the card under the reply offers it: the community has a notebook tab, the page is the chat's dataset and its notebook is available, and a run in the chat has succeeded.
  It takes the same path as the card's button (`runNotebookImport`), is disabled with `aria-busy` while the import runs or a reply loads, and, as for the card, opening the notebook docks the dialog (with a `modechange` event).
- **The plan rail.**
  A plan the conversation holds shows as a rail beside the thread; see [The plan card and step progress](#the-plan-card-and-step-progress).
- **Narrow screens.**
  At 600 px and under the dialog fills the screen, and the header's buttons keep their icons only; at 900 px and under there is no plan rail.
- **Looks.**
  Colors come from the widget's own tokens, so the dark scheme and the community's theme carry over, and the one transition, the pills' hover, is covered by the widget's `prefers-reduced-motion` rule.

### The start screen

While the dialog's conversation is empty it shows board C2 in place of the thread: a heading, a 60 px question field, the chat's own suggestions as 44 px pills, up to three capability cards and a footer.
Any message replaces it with the thread, in a centered column of at most 760 px, and a new chat from History shows it again.
The field writes through to the chat's own input, so docking from the start screen keeps what was typed.
In the corner the start screen is never shown.
The community's disclaimer row stays under it, with its **Send feedback** link; when the community sets a `center_start.note`, the note takes the disclaimer text's place there and the link stays.

The pills are what the chat's suggestions would be: the dataset questions for the dataset on screen, else `suggested_questions`.
The cards, the footer's note and the docs link are the community's, from two `widget` keys; the widget carries no copy of its own about any community.

| Field | Type | What it changes |
|---|---|---|
| `center_start` | `{capabilities, note}` | `capabilities`: up to three `{title, text}` cards (a title of at most 40 characters, text of at most 160). `note`: a line of at most 300 characters in the start screen's footer. All plain text, with no `<` or `>`. Unset, the start screen has the field and the pills alone. |
| `analysis_docs` | `{title, url}` | A docs page. `title`: at most 80 characters, shown as the link's text in Settings. `url`: an `https://` address of at most 500 characters, with a host and no credentials. The start screen's footer gets a **How it works** link, and Settings a **Learn more** row, both opening it in a new tab (`target="_blank"`, `rel="noopener noreferrer"`, and a visually hidden "(opens in a new tab)" for a screen reader). Unset, neither link exists. |

```yaml
widget:
  center_start:
    capabilities:
      - title: Describe
        text: "Summaries, methods and citations, from the dataset's own files."
    note: "Code runs in your browser. Nothing you run is uploaded. AI can make mistakes."
  analysis_docs:
    title: Analyze data in your browser
    url: https://docs.nemar.org/web/analyze-in-browser/
```

Both are validated twice, as the colors are: `WidgetConfig` refuses a bad value when the config loads, and the widget checks again before drawing, so a value the server did not check (an older deploy, an embedder's `setConfig({centerStart, analysisDocs})`) is held to the same limits.
A card that is not within them is left out, and a link whose address is not an `https://` URL is not drawn at all.

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
| `search_paths` | `searchPaths` (a list of paths) |
| `center_start` | `centerStart` (the same `{capabilities, note}`) |
| `analysis_docs` | `analysisDocs` (the same `{title, url}`) |
| `privacy_url` | `privacyUrl` (an `https://` address) |
| `logo_url` | `logo` |
| `theme_color` | `themeColor` |
| `theme_text_color` | `themeTextColor` |
| `accent_color` | `accentColor` |
| `user_bubble_color` | `userBubbleColor` |
| `user_bubble_text_color` | `userBubbleTextColor` |
| `launcher` | `launcher` |
| `launcher_label` | `launcherLabel` |
| `launcher_position` | `launcherPosition` |
| `launcher_size` | `launcherSize` |
| `launcher_open_size` | `launcherOpenSize` |
| `launcher_offset_x` | `launcherOffsetX` |
| `launcher_offset_y` | `launcherOffsetY` |
| `launcher_mobile_offset_x` | `launcherMobileOffsetX` |
| `launcher_mobile_offset_y` | `launcherMobileOffsetY` |
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

The "What happens with a malformed value" section right below covers the color
fields and the launcher's position, sizes and offsets; it does not describe
`notebookUrl` (see above) or `launcher` (any value other than exactly `"capsule"`
behaves as `bubble`, silently, since `setConfig` does not validate it the way it
validates `notebookUrl`).

### What happens with a malformed value

Every color field above is validated twice: `WidgetConfig`'s own
`pattern=r"^#[0-9a-fA-F]{6}$"` refuses a malformed `config.yaml` value at
load time (before the community ever ships), and the widget checks again
before applying anything, in case an embedder's `setConfig` call carries a
typo `config.yaml` never saw. A value that fails the second check is never
applied silently: the widget logs a `console.warn` naming the field and the
rejected value, and falls back to that field's own default, exactly as if
the field had been left unset.

The launcher's position, sizes and offsets (see "Position, size and offsets") follow the same rule, with a range or a set of names in place of a color pattern.
`WidgetConfig` refuses a value outside it at load time.
The widget checks again for `setConfig`, and ignores a bad value with a `console.warn` that names the field, the value and what was expected.
Where the community sets the field too, its value still applies; the bare default is only what an unset field has.
An open size larger than the closed size is brought down to it, with a warning.
A `setConfig` made after `init()` for any of these fields moves or resizes the launcher at once, and clearing one with `null` puts it back to its default.
