# Facility Finder design contract

Every screen in this app follows this file. `ui_theme.py` is the only place colours, type and
spacing are defined; views use its classes and tokens and do not set their own.

## What the product is

A tool people open under stress: someone unwell, a crew choosing a destination, a planner reading
a map. It should look like an instrument, not a landing page. Plain surfaces, hairlines, exact
numbers, and one colour that means "emergency".

## Bans

- No gradients (`linear-gradient`, `radial-gradient`) and no glass (`backdrop-filter`, blur).
- No drop shadows on anything we draw. Regions are separated by a 1px hairline or a surface shift.
- No rounded "cards with shadow". Radius is 4px everywhere, 0 on tables and dividers.
- No indigo, purple or violet. No second accent colour.
- No emoji anywhere in the interface. Icons only where they carry meaning (location), never as decoration.
- No pill badges, no coloured tag chips, no status dots that do not encode data.
- No uppercase labels, no letter-spaced eyebrows, no label above a heading.
- No stat-card grid with tinted tiles. Figures sit in one ruled row.
- No animation except Streamlit's own loading states.
- No filler copy: no greetings, no "here you will see", no sentence that restates the heading.

## Colour (one ramp per theme, plus two meanings)

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#FFFFFF` | `#0C0D0E` | page |
| `--surface` | `#F5F5F4` | `#161718` | inputs, the person's chat bubble, table header |
| `--border` | `#E3E3E0` | `#2A2B2D` | every hairline |
| `--ink` | `#111213` | `#EDEDEC` | text, primary buttons |
| `--muted` | `#62646A` | `#9A9CA1` | secondary text |
| `--accent` | `#0B6B5D` | `#45C4AD` | selection, links, the one data series |
| `--red` | `#B42318` | `#FF6A5E` | emergency only |
| `--warn` | `#8A5A00` | `#E3B25A` | data caveats only |

Evidence groups are data, so they keep a fixed scale: stated `#2E8B62`, alternative `#7DB89A`,
part `#C9941F`, label only `#C4652B`, none `#8B8D93`. They appear as a 8px square swatch next to
plain text, and as bar fills. Red is never used for data.

## Type

- UI: **Geist** (400, 500, 600). Fallback `system-ui`.
- Numbers, times, distances, scores, phone numbers, IDs: **JetBrains Mono** with `tabular-nums`.
- Scale (px): 12, 13, 14, 16, 20, 28, 40. Nothing else.
- Headings are 600 weight, sentence case, tight tracking (-0.02em at 28 and above). Body is 16/24.
- One h1 per view. No bold inside body copy except facility names and the action ("Call 108").

## Spacing and shape

- Spacing scale (px): 4, 8, 12, 16, 24, 32, 48. Nothing else.
- Radius 4px. Borders 1px `--border`. Focus ring 2px `--accent`, offset 2px.
- Content width 1200px. The patient start screen is one 720px column.
- Tables: no outer box, header in `--muted` 13px, rows ruled by hairlines, numbers right-aligned in mono.

## Copy

- Say the thing. Headings are the question the screen answers.
- Facilities "say" or "state" things. Nothing is verified, and no number implies a chance that a service exists.
- Buttons name the action: "Find hospitals", "Use my location", "Start again".
- The emergency line is one sentence and comes first on every view.

## Patient view rules

- First screen: the question, the input, the symptom choices, the location. Nothing above the input but the emergency bar.
- An emergency answer starts with "Call 108 now" before any other text.
- Evidence beside the chat shows only real fields: time, distance, care level, what the facility states, 24x7 claim, beds, phone.
