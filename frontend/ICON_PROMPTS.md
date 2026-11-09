# Icon prompts

The dashboard shows a simple line icon for each page until you add your own image. To use your own,
generate each image below (Gemini, or any image generator), save it in `frontend/public/icons/` with
the exact file name shown, and reload the page. Nothing else needs to change. A missing or misnamed
file just keeps the line icon.

**Size:** square PNG, 256 × 256 pixels (if the generator gives 1024 × 1024, resize it, for example in
Paint: Resize > Pixels > 256). **Login background:** about 1600 × 1000 pixels, saved as `login-hero.jpg`.

The icons are full-bleed tiles (colour all the way to the edges). The dashboard rounds the corners
itself, so no transparent background is needed and they look right on both the dark side panel and the
white pages.

## Shared style (already included in every prompt below)

> Modern flat app icon. One bold glyph in pure white with rounded line ends, centred, filling about 55%
> of the canvas. The background fills the entire square edge to edge (no rounded corners, no border, no
> margin, no drop shadow) with a smooth diagonal gradient from COLOUR A (top left) to COLOUR B (bottom
> right) and a very soft highlight in the top-left corner. No text, letters, numbers or watermark.
> Square, 1:1.

## Prompts

| File | Used for | Prompt |
| --- | --- | --- |
| `brand.png` | Logo in the side panel and the browser tab | Modern flat app icon. One bold glyph in pure white with rounded line ends, centred, filling about 55% of the canvas: a shield with a check mark inside, and three tiny connected dots along the bottom edge of the shield like a small network. The background fills the entire square edge to edge (no rounded corners, no border, no margin, no drop shadow) with a smooth diagonal gradient from #3B82F6 (top left) to #7C3AED (bottom right) and a very soft highlight in the top-left corner. No text, letters, numbers or watermark. Square, 1:1. |
| `investigate.png` | Investigate page | Modern flat app icon. One bold glyph in pure white with rounded line ends, centred, filling about 55% of the canvas: a magnifying glass whose lens shows three small dots joined by lines, like a tiny network graph. The background fills the entire square edge to edge (no rounded corners, no border, no margin, no drop shadow) with a smooth diagonal gradient from #3B82F6 (top left) to #1D4ED8 (bottom right) and a very soft highlight in the top-left corner. No text, letters, numbers or watermark. Square, 1:1. |
| `cases.png` | Cases page | Modern flat app icon. One bold glyph in pure white with rounded line ends, centred, filling about 55% of the canvas: a document folder with a small pinned tag on its front. The background fills the entire square edge to edge (no rounded corners, no border, no margin, no drop shadow) with a smooth diagonal gradient from #F59E0B (top left) to #EA580C (bottom right) and a very soft highlight in the top-left corner. No text, letters, numbers or watermark. Square, 1:1. |
| `status.png` | Status page | Modern flat app icon. One bold glyph in pure white with rounded line ends, centred, filling about 55% of the canvas: a heartbeat pulse line running across a small server rack. The background fills the entire square edge to edge (no rounded corners, no border, no margin, no drop shadow) with a smooth diagonal gradient from #10B981 (top left) to #047857 (bottom right) and a very soft highlight in the top-left corner. No text, letters, numbers or watermark. Square, 1:1. |
| `sources.png` | Sources page | Modern flat app icon. One bold glyph in pure white with rounded line ends, centred, filling about 55% of the canvas: an open book with a bookmark ribbon and a small link-chain symbol above it. The background fills the entire square edge to edge (no rounded corners, no border, no margin, no drop shadow) with a smooth diagonal gradient from #06B6D4 (top left) to #0E7490 (bottom right) and a very soft highlight in the top-left corner. No text, letters, numbers or watermark. Square, 1:1. |
| `bigquery.png` | BigQuery refresh page | Modern flat app icon. One bold glyph in pure white with rounded line ends, centred, filling about 55% of the canvas: a database cylinder made of three stacked discs, with a circular refresh arrow curving around it. The background fills the entire square edge to edge (no rounded corners, no border, no margin, no drop shadow) with a smooth diagonal gradient from #8B5CF6 (top left) to #5B21B6 (bottom right) and a very soft highlight in the top-left corner. No text, letters, numbers or watermark. Square, 1:1. |
| `users.png` | Users page (admins only) | Modern flat app icon. One bold glyph in pure white with rounded line ends, centred, filling about 55% of the canvas: two people shown as head-and-shoulders outlines, one slightly behind the other, with a small key beside them. The background fills the entire square edge to edge (no rounded corners, no border, no margin, no drop shadow) with a smooth diagonal gradient from #EC4899 (top left) to #9D174D (bottom right) and a very soft highlight in the top-left corner. No text, letters, numbers or watermark. Square, 1:1. |
| `account.png` | Your account page | Modern flat app icon. One bold glyph in pure white with rounded line ends, centred, filling about 55% of the canvas: a single person head-and-shoulders outline inside a circle. The background fills the entire square edge to edge (no rounded corners, no border, no margin, no drop shadow) with a smooth diagonal gradient from #64748B (top left) to #1E293B (bottom right) and a very soft highlight in the top-left corner. No text, letters, numbers or watermark. Square, 1:1. |
| `login-hero.jpg` | Background of the sign-in page (shown faintly; JPEG keeps it small) | Wide, calm illustration for the sign-in screen of a blockchain payments compliance tool. Deep navy background (#0B1222). A softly glowing abstract network of round nodes joined by thin lines flows from left to right in blue (#3B82F6) and violet (#7C3AED); a few nodes glow brighter like highlighted transactions. Gentle depth of field, minimal, lots of calm empty space in the centre left. No text, no logos, no coins, no people. Landscape 16:10, 1600 × 1000 pixels. |

## Tips

- Generate the eight square icons in one session so they come out in the same style. If one looks
  different, ask the generator to "match the style of the previous icon exactly".
- Keep the glyphs simple: they are shown as small as 20 pixels in the side panel.
- To go back to the line icon for a page, delete or rename its file.
