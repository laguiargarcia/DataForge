# Visual identity of ninho

Everything made for the ninho front follows this identity (Lucas, 2026-11-24): screens, icons, the
installable app's icon and splash, any page a person sees. This file is the written form of the
identity sheet, so nobody has to open the image to know a colour or a font.

| File | What it is |
| --- | --- |
| `logo-ninho.png` | The logo: the illustration with the word "ninho" under it. 342 × 300 px, transparent background. |
| `identidade-visual-original.png` | The identity sheet as received (1920 × 1080): the logo, the two fonts and the three colours. The source of this file; do not edit it. |

## Logo

Two people embraced, wrapped in one striped blanket that closes into a round nest, with small leafy
branches on both sides, and the word **ninho** in lower case below. The drawing is hand-made: uneven
line, soft shapes, no hard corners.

- The drawing and the word are in the brand blue; the branches are in the brand green.
- Use it on the cream background or on white. The background of the file is transparent.
- Do not recolour it, stretch it, crop the branches or separate the word from the drawing. When only
  a square mark fits (an app icon), use the drawing without the word, on cream.
- It is a raster file, 342 × 300: fine up to about that size on screen. A larger use (splash screen,
  a store icon) needs a vector or a larger export from Lucas.

## Colours

| Name | Hex | Where it comes from | Use |
| --- | --- | --- | --- |
| Blue (azul) | `#99b2d9` (see the note) | the logo's line and the word "ninho" | the brand colour: the logo, large titles, fills, the active item |
| Cream (creme) | `#f8f0de` | the sheet | the background of the app |
| Green (verde) | `#addcaa` | the branches of the logo | the second colour: positive marks, small accents |

**Note on the blue.** The sheet writes it as `#99b29`, five digits, so one is missing. Measured in
`logo-ninho.png`, the blue of the line is about `#9ab1d5` (most pixels between `#98b0d0` and
`#9cb4d8`), which agrees with `99 b2` and a last pair near `d6`. `#99b2d9` is the reading used
until Lucas gives the exact value.

**Text needs a darker colour.** The three colours are all light: the blue on cream has a contrast of
about 2 to 1, far below what running text needs (4.5 to 1). So the brand blue is for the logo, large
titles and filled shapes, never for body text or small labels. The sheet gives no dark colour; the
ink for text, and the colours for error and warning, are chosen when the identity is applied to the
screens, as darker tones that sit well with these three, and are then written in this table.

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

- The exact blue (the sheet's value has a digit missing).
- Whether Lucas has a licence and the files of Migra for use on the web, or which serif stands in.
- A vector version of the logo, and the drawing without the word for the app icon.
- The dark colour for text and the error colour.
