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

```html
<script src="https://widget.osc.earth/osa/osa-chat-widget.js" data-no-auto-init></script>
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

### What happens with a malformed value

Every color field above is validated twice: `WidgetConfig`'s own
`pattern=r"^#[0-9a-fA-F]{6}$"` refuses a malformed `config.yaml` value at
load time (before the community ever ships), and the widget checks again
before applying anything, in case an embedder's `setConfig` call carries a
typo `config.yaml` never saw. A value that fails the second check is never
applied silently: the widget logs a `console.warn` naming the field and the
rejected value, and falls back to that field's own default, exactly as if
the field had been left unset.
