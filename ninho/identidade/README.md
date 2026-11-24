# Visual identity of ninho

Everything made for the ninho front follows this identity (Lucas, 2026-11-24): screens, icons, the
installable app's icon and splash, any page a person sees. This file is the written form of the
identity sheet, so nobody has to open the image to know a colour or a font.

| File | What it is |
| --- | --- |
| `logo-ninho.svg` | The logo: the drawing with the word "ninho" under it, sharp at any size (how it was made: below). |
| `desenho-ninho.svg` | The drawing without the word, the same way: the source of the square icons. |
| `desenho-ninho.png` | The drawing without the word, from Lucas (2026-11-24): 644 × 554 px, transparent background. It came as 2000 × 1200 on opaque black; the black was turned into transparency (brightness as opacity, so the soft edges of the branches stay) and the image cropped to the drawing. The source of both SVGs. |
| `identidade-visual-original.png` | The identity sheet as received (1920 × 1080): the logo, the two fonts and the three colours. The source of this file; do not edit it. |

## Logo

Two people embraced, wrapped in one striped blanket that closes into a round nest, with small leafy
branches on both sides, and the word **ninho** in lower case below. The drawing is hand-made: uneven
line, soft shapes, no hard corners.

- The drawing and the word are in the brand blue; the branches are in the brand green.
- Use it on the cream background or on white. The background of the file is transparent.
- Do not recolour it, stretch it, crop the branches or separate the word from the drawing. When only
  a square mark fits (an app icon), use the drawing without the word, on cream.
- The screens' icons (`ninho/telas/public/icone-64.png`, the tab icon, and `icone-180.png`, the
  iPhone home-screen icon) are `desenho-ninho.svg` centred on cream at 86 % of the side.
- **How the SVGs were made** (df-8x0m.23.28; the person who drew the logo has no vector). They are
  hybrid. The blue lines of `desenho-ninho.png` and the word "ninho", taken from
  `identidade-visual-original.png` where it is largest, were traced into vector shapes (vtracer,
  each colour on its own, enlarged 4 × and smoothed before tracing), so they stay sharp at any size.
  The green branches are the PNG itself (their measured green is `#a3d8a0`, a touch darker than the
  sheet's `#addcaa`), embedded in the SVG under the lines: their sprayed, grainy
  texture is what a trace loses, and SVG noise filters tried to imitate it did not come close
  (and would render differently in Safari). The branches are 611 px wide, so at a very large size
  (a poster) they get softer than the lines; on any screen they are fine.

## Colours

| Name | Hex | Where it comes from | Use |
| --- | --- | --- | --- |
| Blue (azul) | `#99b2d9` | the logo's line and the word "ninho" | the brand colour: the logo, fills, the active item (titles use the dark blue below) |
| Cream (creme) | `#f8f0de` | the sheet | the background of the app |
| Green (verde) | `#addcaa` | the branches of the logo | the second colour: positive marks, small accents |

**Note on the blue.** The sheet writes it as `#99b29`, five digits, one missing; Lucas confirmed
`#99b2d9` (2026-11-24, df-8x0m.23.26).

**Text needs a darker colour.** The three colours are all light: the blue on cream has a contrast of
about 1.9 to 1, far below what running text needs (4.5 to 1). So the brand blue is for the logo and
filled shapes (buttons, the active item), never for text. The dark colour is
`#041e42`, a deep navy given by Lucas (2026-11-24) apart from the sheet; the other tones were derived
when the identity was applied to the screens (df-8x0m.23.25), each a darker shade of the sheet's
hues, with its contrast measured against the cream:

| Name | Hex | Contrast on cream | Use |
| --- | --- | --- | --- |
| Ink (texto) | `#041e42` | 14.6 : 1 | the identity's dark colour: running text; also the text on a brand-blue button (7.7 : 1 on `#99b2d9`) |
| Secondary (secundário) | `#5b6275` | 5.4 : 1 | dates, labels, empty states |
| Dark blue (acento) | `#3d5a8f` | 6.0 : 1 | the screen titles and text links: the brand blue, darkened |
| Dark green (entrada) | `#2f6b3a` | 5.6 : 1 | positive amounts and the tick of a bought item: the brand green, darkened |
| Error (alerta) | `#a33b2c` | 5.8 : 1 | error text, on cream or on its own light fill `#f8e0d8` (5.2 : 1) |
| Card (cartão) | `#fffbf2` | | the light surface of lists and cards, on cream |

The screen titles use the dark blue rather than the brand blue: the brand blue on cream does not
reach even the 3 : 1 that large text needs. The values live in `ninho/telas/src/index.css`, which
names them as Tailwind colours (`texto`, `acento`, `marca`, `verde`...).

## Fonts

| Role | Font | Notes |
| --- | --- | --- |
| Title (título) | **Migra, bold** | A high-contrast display serif with sharp, hooked terminals; the word "ninho" in the logo is set in it. Commercial font (Pangram Pangram): its files may not be committed to this public repository unless the licence allows it. Until that is settled, titles fall back to a serif of the system. |
| Subtitle and text (subtítulo) | **Quicksand** | A rounded geometric sans, light and friendly. Open licence (SIL OFL), so it can be bundled with the front (`@fontsource/quicksand`). |

Titles are few and large (the name of a screen, the app's name); everything else is Quicksand.

## Feel

Soft, warm and domestic: light colours, rounded shapes, generous space, nothing sharp or corporate.
A screen should look like it belongs next to the drawing.

## Still open

- Whether Lucas has a licence and the files of Migra for use on the web. Until then the titles use
  the system serif (`ui-serif`, Georgia).
