# CJK Font Support

RebbleOS currently renders Chinese notification text as tofu boxes because
the system Gothic fonts do not contain CJK glyphs. The notification parser
preserves UTF-8 text; the missing piece is glyph coverage.

## Constraints

Pebble `.pbf` fonts are bitmap fonts. Adding all Simplified and Traditional
Chinese glyphs to every Gothic size is not practical:

- Every glyph stores bitmap data plus per-glyph metadata and lookup table
  entries.
- The current PBF hash table stores per-bucket counts in `uint8_t` and offset
  table offsets in `uint16_t`, so very large all-CJK fonts exceed the format
  before flash size is the only problem.
- Existing resource packs for classic platforms leave limited room; Asterix
  has more resource flash, but full CJK at multiple sizes is still wasteful.

The best practice is therefore:

1. Use an OFL-compatible CJK source font, such as
   [Noto Sans CJK](https://github.com/notofonts/noto-cjk) or
   [Source Han Sans](https://github.com/adobe-fonts/source-han-sans). Do not
   generate distributable assets from proprietary system fonts.
2. Generate a CJK subset from a charset or notification corpus.
3. Prefer one notification fallback font size first, then add more sizes only
   after measuring resource cost.
4. Keep Latin text on the existing Renaissance/Gothic fonts and use the CJK
   font only as a fallback for codepoints missing from the primary font.

Noto Sans CJK and Source Han Sans both publish region-specific Simplified and
Traditional downloads. Their upstream documentation recommends region-specific
subset fonts when only one region's glyphs are needed. For very large source
fonts, use [fontTools subset](https://fonttools.readthedocs.io/en/latest/subset/)
first, then convert the reduced outline font to `.pbf`.

## Generating A PBF

Install the Python dependencies, then generate a test font:

```sh
python3 -m pip install -r Utilities/requirements.txt
python3 Utilities/mkcjkfont.py \
  --font /path/to/NotoSansCJKsc-Regular.otf \
  --charset-file Utilities/cjk/notification_zh_seed.txt \
  --size 18 \
  --line-height 20 \
  --output build/cjk-notification-18.pbf \
  --preview build/cjk-notification-18.txt
```

For Traditional Chinese, use a TC/TW or HK source font and a Traditional
charset. For mixed Simplified/Traditional notifications, merge the charset
files and generate a single fallback font, then check the resulting `.pbf`
size.

The seed charset in `Utilities/cjk/notification_zh_seed.txt` is deliberately
small. It is useful for smoke tests, not for production coverage. A production
charset should come from a real notification corpus or from a documented
common-character standard, then be measured against platform resource limits.

## Runtime Integration

The renderer asks one font for every glyph. Missing glyphs are replaced with
the font's fallback box, so RebbleOS adds an optional CJK fallback hook in the
font loader. If the platform resource header defines
`RESOURCE_ID_CJK_NOTIFICATION_18`, the fallback font is loaded lazily the first
time a CJK glyph is needed. If that resource is absent, behavior is unchanged.

The glyph lookup path is:

1. Try the requested Gothic/Renaissance font.
2. If the glyph is absent and the codepoint is CJK or full-width punctuation,
   try the configured CJK fallback font.
3. If that also fails, draw tofu.

This keeps existing UI metrics for English text and avoids bloating every
system font with the same CJK bitmaps. The fallback font should be generated
with a line height close to the notification font it supplements; line layout
still uses the primary font's line height.

After adding a generated `.pbf` to a resource pack, expose it as
`RESOURCE_ID_CJK_NOTIFICATION_18`. The system renderer will then load it
automatically per app or overlay thread when Chinese text is first rendered.
Code that needs to override the fallback can still register a custom font:

```c
GFont cjk_font = fonts_get_system_font(FONT_KEY_CJK_NOTIFICATION_18);
fonts_set_cjk_fallback_font(cjk_font);
```

Without `RESOURCE_ID_CJK_NOTIFICATION_18`, the fallback hook is empty and the
firmware keeps its previous behavior.

Do not call `fonts_set_cjk_fallback_font()` with a temporary custom font unless
the same code also unloads it through `fonts_unload_custom_font()`. Unloading a
registered custom fallback clears the hook automatically.
