#include "fonts.h"
#include "font_loader.h"
#include "fs.h"

#define FONT_TOFU_CACHE_BIT 0x80000000UL

static bool _font_codepoint_is_cjk_fallback_candidate(uint32_t codepoint)
{
    return (codepoint >= 0x2E80 && codepoint <= 0x2EFF) ||
        (codepoint >= 0x3000 && codepoint <= 0x303F) ||
        (codepoint >= 0x31C0 && codepoint <= 0x31EF) ||
        (codepoint >= 0x3400 && codepoint <= 0x4DBF) ||
        (codepoint >= 0x4E00 && codepoint <= 0x9FFF) ||
        (codepoint >= 0xF900 && codepoint <= 0xFAFF) ||
        (codepoint >= 0xFF00 && codepoint <= 0xFFEF);
}

static uint32_t _font_tofu_cache_codepoint(uint32_t codepoint)
{
    return codepoint | FONT_TOFU_CACHE_BIT;
}

static uint32_t _font_read_codepoint(uint8_t *offset_entry,
    uint8_t codepoint_bytes)
{
    uint32_t codepoint = 0;

    memcpy(&codepoint, offset_entry, codepoint_bytes);
    return codepoint;
}

static uint32_t _font_read_glyph_offset(uint8_t *offset_entry,
    uint8_t codepoint_bytes, uint8_t features)
{
    uint32_t offset = 0;

    if (features & n_GFontFeature2ByteGlyphOffset)
        memcpy(&offset, offset_entry + codepoint_bytes, sizeof(uint16_t));
    else
        memcpy(&offset, offset_entry + codepoint_bytes, sizeof(uint32_t));

    return offset;
}

static n_GGlyphInfo *_font_load_glyph_info(struct file *font,
    uint32_t codepoint, bool *found, bool use_tofu)
{
    struct fd fd;
    n_GFontInfo info;
    n_GGlyphInfo pglyph;
    bool matched = false;

    if (found)
        *found = false;

    n_GGlyphInfo *cglyph = fonts_glyphcache_get(font, codepoint);

    if (cglyph) {
        if (found)
            *found = true;
        return cglyph;
    }

    cglyph = fonts_glyphcache_get(font, _font_tofu_cache_codepoint(codepoint));

    if (cglyph)
        return use_tofu ? cglyph : NULL;

    /* XXX: cache this */
    fs_open(&fd, font);
    fs_read(&fd, &info, sizeof(info));

    uint8_t hash_table_size = 255, codepoint_bytes = 4, features = 0;
    switch (info.version) {
        case 1:
            fs_seek(&fd, __FONT_INFO_V1_LENGTH, FS_SEEK_SET);
            break;
        case 2:
            fs_seek(&fd, __FONT_INFO_V2_LENGTH, FS_SEEK_SET);
            break;
        default:
            fs_seek(&fd, info.fontinfo_size, FS_SEEK_SET);
    }
    switch (info.version) {
        // switch trickery! Default first is valid.
        default:
            features = info.features;
        case 2:
            hash_table_size = info.hash_table_size;
            codepoint_bytes = info.codepoint_bytes;
        case 1:
            break;
    }

    long loc = fs_seek(&fd, 0, FS_SEEK_CUR);

    uint8_t offset_table_item_length = codepoint_bytes +
        (features & n_GFontFeature2ByteGlyphOffset ? 2 : 4);

    /* Read the codepoint from the hash table ... */
    fs_seek(&fd,
        (codepoint % hash_table_size) * sizeof(n_GFontHashTableEntry),
        FS_SEEK_CUR);

    n_GFontHashTableEntry hash_data;
    fs_read(&fd, &hash_data, sizeof(hash_data));

    /* ... and seek past the rest of the hash table. */
    loc = fs_seek(&fd, loc + hash_table_size * sizeof(n_GFontHashTableEntry),
        FS_SEEK_SET);

    if (hash_data.hash_value != (codepoint % hash_table_size)) {
        if (!use_tofu)
            return NULL;

        // There was no hash table entry with the correct hash. Use tofu.
        fs_seek(&fd, offset_table_item_length * info.glyph_amount + 4,
            FS_SEEK_CUR);
        goto readglyph;
    }

    /* It exists, so we find it in the offset table. */
    fs_seek(&fd, loc + hash_data.offset_table_offset, FS_SEEK_SET);
    uint8_t offset_entry[8]; /* 4 bytes codepoint, 4 bytes glyph offset */

    for (uint16_t i = 0; i < hash_data.offset_table_size; i++) {
        fs_read(&fd, offset_entry, offset_table_item_length);

        if (_font_read_codepoint(offset_entry, codepoint_bytes) == codepoint) {
            matched = true;
            break;
        }
    }

    if (!matched) {
        if (!use_tofu)
            return NULL;

        // We couldn't find the correct entry. Use tofu.
        fs_seek(&fd, loc + offset_table_item_length * info.glyph_amount + 4,
            FS_SEEK_SET);
        goto readglyph;
    }

    if (found)
        *found = true;

    fs_seek(&fd, loc + offset_table_item_length * info.glyph_amount +
        _font_read_glyph_offset(offset_entry, codepoint_bytes, features),
        FS_SEEK_SET);

readglyph:
    /* How many bytes is a glyph? */
    fs_read(&fd, &pglyph, sizeof(pglyph));
    int gbits = pglyph.height * pglyph.width;
    int gbytes = (gbits + 7) / 8;

    n_GGlyphInfo *glyph = malloc(sizeof(pglyph) + gbytes);
    memcpy(glyph, &pglyph, sizeof(pglyph));
    fs_read(&fd, glyph + 1, gbytes);

    fonts_glyphcache_put(font,
        matched ? codepoint : _font_tofu_cache_codepoint(codepoint), glyph);

    return glyph;
}

uint8_t n_graphics_font_get_line_height(struct file *font) {
    struct fd fd;
    n_GFontInfo info;
    
    /* XXX: cache this */
    fs_open(&fd, font);
    fs_read(&fd, &info, sizeof(info));
    return info.line_height;
}

n_GGlyphInfo * n_graphics_font_get_glyph_info(struct file *font, uint32_t codepoint) {
    bool found = false;
    n_GGlyphInfo *glyph = _font_load_glyph_info(font, codepoint, &found,
        false);

    if (found)
        return glyph;

    if (_font_codepoint_is_cjk_fallback_candidate(codepoint)) {
        GFont fallback = fonts_get_cjk_fallback_font();

        if (fallback && fallback != font) {
            glyph = _font_load_glyph_info(fallback, codepoint, &found, false);
            if (found) {
                _font_load_glyph_info(font, codepoint, NULL, true);
                return glyph;
            }
        }
    }

    glyph = _font_load_glyph_info(font, codepoint, NULL, true);

    return glyph;
}

void n_graphics_font_draw_glyph_bounded(n_GContext * ctx, n_GGlyphInfo * glyph,
    n_GPoint p, int16_t minx, int16_t maxx, int16_t miny, int16_t maxy) {
    p.x += glyph->left_offset;
    p.y += glyph->top_offset;
    for (uint8_t y = 0; y < glyph->height; y++)
        for (uint8_t x = 0; x < glyph->width; x++)
            if (glyph->data[(y*glyph->width+x)/8] & (1 << ((y*glyph->width+x) % 8)) &&
                    p.x + x >= minx && p.x + x < maxx &&
                    p.y + y >= miny && p.y + y < maxy)
                n_graphics_set_pixel(ctx, n_GPoint(p.x + x, p.y + y), ctx->text_color);
}

void n_graphics_font_draw_glyph(n_GContext * ctx, n_GGlyphInfo * glyph, n_GPoint p) {
    n_graphics_font_draw_glyph_bounded(ctx, glyph, p, 0, __SCREEN_WIDTH, 0, __SCREEN_HEIGHT);
}
