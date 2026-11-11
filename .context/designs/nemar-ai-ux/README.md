# NEMAR AI experience: design references

Static mockups for the epics in `.context/nemar-ai-ux-plan.md`.
They are Design Component pages exported from the review canvas: read them as markup and style references, not as code to ship.
Inline styles carry the intended colors, spacing and copy; `<sc-if>` blocks show alternate states.

| Board | File | Epic |
|---|---|---|
| A1 New dataset: reset on next message | `WidgetSwitch.dc.html` | E1 |
| A2 Plan first, then run | `WidgetPlan.dc.html` | E2 |
| A3 Step results, code, errors | `WidgetStep.dc.html` | E2, E3 |
| A4 Fresh chat on return and history | `WidgetHistory.dc.html` | E1 |
| A5 Settings: memory and run mode | `WidgetSettings.dc.html` | E2, E5 |
| A6 Handover to the analysis model | `WidgetHandoff.dc.html` | E8 |
| B1 Continue in notebook, named notebook tabs | `Notebook.dc.html` | E4 |
| C1 Dataset page: AI Mode pill | `Main.dc.html` | E6 |
| C2 AI Mode opens on the dataset | `CenterStart.dc.html` | E6 |
| C3 AI Mode conversation | `CenterStage.dc.html` | E2, E6 |
| D1 Home: AI Mode toggle in search | `Home.dc.html` | E6 |
| D2 Discover with AI overview | `Discover.dc.html` | E6 |

Colors: the widget header keeps nemar.org's teal `#5bbad5` with navy text `#06112a`;
assistant accents use the darker teal `#0e7490`, which passes 4.5:1 on white.
"AI Mode" is one pill across the site, at the 44 px pill height of `.action` and the search button: brand teal `#5bbad5` with `#04121f` text as an action, a teal tint `#e3f4f9` with `#0b4f63` text as a toggle that is on.
When AI Mode is on in a search field, the field takes the hero's teal-to-purple gradient (`#5bbad5` to `#603cba`) as its ring.
