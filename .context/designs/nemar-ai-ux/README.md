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
| C1 Dataset page: ask this dataset | `Main.dc.html` | E6 |
| C2 Center-stage conversation | `CenterStage.dc.html` | E2, E6 |
| C3 Discover with AI overview | `Discover.dc.html` | E6 |

Colors: the widget header keeps nemar.org's teal `#5bbad5` with navy text `#06112a`;
assistant accents use the darker teal `#0e7490`, which passes 4.5:1 on white.
