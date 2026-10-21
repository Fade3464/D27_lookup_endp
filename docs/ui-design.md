# D27 Desk UI design

The UI uses the community [Apple Design skill](https://github.com/dickwu/apple-design-skill),
downloaded into `skills/apple-design/`. It is not an Apple product or official
Apple skill. No downloaded scripts were run.

## Context and assessment

This is a React web utility for campaign operators. Its primary task is to
choose a campaign, check a number, and understand its verdict. Apple's
accessibility, layout, typography, color and restraint principles apply here;
native macOS window controls, menu bars and gestures do not.

The previous interface gave a large green sidebar and a marketing headline more
prominence than the actual check. Small fixed-color labels did not adapt to dark
appearance. The redesign gives priority to the campaign, phone number and result.
The signature is the pairing of campaign radio tiles with a compact verdict and
its check details. This follows the product's actual decision rather than adding
unrelated dashboard metrics.

## Tokens

These are application tokens, not claimed copies of Apple's system colors.
Ratios below use the WCAG relative-luminance formula and actual hex values.

| Role | Light | Dark | Verified contrast |
| --- | --- | --- | --- |
| Canvas | `#f5f5f7` | `#151517` | Background only |
| Surface | `#ffffff` | `#1c1c1e` | Background only |
| Primary content | `#1d1d1f` | `#f5f5f7` | 16.83:1 / 15.63:1 on surface |
| Secondary content | `#64646b` | `#a8a8b0` | 5.87:1 / 7.21:1 on surface |
| Action accent | `#0066cc` | `#69adff` | 5.57:1 / 7.31:1 on surface |
| Success signal | `#176b3a` on `#eaf5ed` | `#8ed9a8` on `#20372a` | Light 5.87:1; text and check icon accompany color |

Type uses the installed system UI font, with platform fallbacks. No Apple fonts
are downloaded or bundled. Titles are 32 px, section titles 16 px, controls and
body text 13–17 px, and secondary data 11–12 px. Type and spacing use `rem` units
to follow browser font-size settings. Numeric phone values use tabular figures.

## Layout

Regular widths keep navigation separate from opaque content. Compact widths
convert navigation to a horizontal row and stack the form, result and table.

```text
Regular
┌─────────────┬──────────────────────────────────────────┐
│ D27 Desk    │ Toolbar                         Account  │
│             ├──────────────────────────────────────────┤
│ Check       │ Suppression Check                        │
│ History     │ ┌────────────────────┐  Context          │
│ My Account  │ │ Campaign A / B     │                   │
│             │ │ Phone    [Check]   │                   │
│             │ └────────────────────┘                   │
│             │ Verdict · number · campaign              │
│             │ Recent lookups                           │
└─────────────┴──────────────────────────────────────────┘

Compact
┌──────────────────────────┐
│ D27 Desk                 │
│ Check | History | Account│
├──────────────────────────┤
│ Suppression Check        │
│ Campaign A / Campaign B  │
│ Phone                    │
│ [Check Number]           │
│ Verdict and record       │
│ History (scrollable)     │
└──────────────────────────┘
```

## Applied references

- `layout.md` › Visual hierarchy: “Order content by relative importance.” The
  check form is first, and raw response details use progressive disclosure.
- `typography.md` › System fonts: use the platform font through a CSS system font
  stack, with a consistent hierarchy and scalable units.
- `color.md` › Inclusive color: status text and different icons accompany color;
  light, dark and high-contrast tokens are provided.
- `accessibility.md` › Vision and Mobility: label inputs and icon-only buttons,
  retain native radio semantics, use visible focus, provide a skip link, and
  keep controls at least 44 px tall.
- `buttons.md` › Style: one prominent action per form, with named loading states
  and press feedback. Secondary navigation remains quiet.
- `sidebars.md` › Best practices: one level of navigation with a hide/show
  control. Browser behavior remains standard.
- `lists-and-tables.md` › Content and Style: readable headings, aligned columns,
  subtle alternating rows and selected-row feedback.
- `text-fields.md` › Best practices: persistent labels, hints, logical keyboard
  order and a telephone keyboard for ANI.
- `liquid-glass.md` › The two layers: translucency is restricted to navigation
  and the toolbar. Cards, forms, results and rows stay opaque.
- `cross-platform.md`: translate native design concepts to CSS tokens, media
  queries, ARIA and Lucide icons rather than imitating native window chrome.

## Review route and limits

Open `/design-preview` to review populated, suppressed, error and empty states.
Its banner identifies all data as fictional. Navigation, filtering, response
disclosure and the sign-in screen are available for review; no API calls,
password changes or database writes occur in this route. Real sign-in and
existing integration remain at `/`.

System light/dark, increased contrast, reduced motion and reduced transparency
have CSS treatments. Browsers that do not expose reduced-transparency preference
cannot report that setting; the navigation remains highly opaque by default.
The design avoids entrance animations and unnecessary decorative accessories.

Verified in this pass: TypeScript and production build; local HTML and asset
responses; anonymous history requests still return 401; measured token contrast;
and DOM component checks for navigation, record details, all preview result
states, sidebar visibility, and zero API calls from the preview. The real root
route still bootstraps session authentication. The DOM test utility was installed
only in `/tmp`, without adding application dependencies.

Visual browser verification, zoom reflow and assistive-technology behavior require
an available browser; those checks are not claimed from source inspection or DOM
component tests alone.

## Scrub job deletion and dialogs

Queued/paused job deletion uses a short confirmation with Cancel focused first.
The shared native dialog for deletion, pause/next-file decisions, and safety
review uses fixed viewport positioning, zero insets, automatic margins, and
fit-content height. Its maximum height and scrolling keep controls reachable
on smaller screens. Color is accompanied by a Delete label and trash icon.
Component checks cover cancellation/Escape, confirmed removal, server conflicts,
retry, and offline preview isolation. No browser was connected for screenshot
or rendered geometry verification in this session.
