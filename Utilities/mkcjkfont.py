#!/usr/bin/env python3
"""Build a Pebble bitmap font from a CJK outline font subset."""

import argparse
import math
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


HASH_TABLE_SIZE = 255
FALLBACK_CODEPOINT = 0x25AF


class Glyph:
    def __init__(self, width, height, left, top, advance, bits):
        self.width = width
        self.height = height
        self.left = left
        self.top = top
        self.advance = advance
        self.bits = bits


def parse_args():
    parser = argparse.ArgumentParser(
        description='Build a RebbleOS/Pebble .pbf bitmap font from a '
                    'subset of an outline CJK font.'
    )
    parser.add_argument(
        '--font',
        required=True,
        help='Path to an OFL-compatible .otf/.ttf/.ttc source font.',
    )
    parser.add_argument(
        '--font-index',
        type=int,
        default=0,
        help='Font index for .ttc/.otc collections. Default: 0.',
    )
    parser.add_argument(
        '--charset-file',
        action='append',
        default=[],
        help='UTF-8 text file whose characters should be included. '
             'May be passed more than once. Lines starting with # are '
             'ignored.',
    )
    parser.add_argument(
        '--text',
        action='append',
        default=[],
        help='Literal UTF-8 characters to include. May be passed more '
             'than once.',
    )
    parser.add_argument(
        '--size',
        type=int,
        default=18,
        help='Source font pixel size. Default: 18.',
    )
    parser.add_argument(
        '--line-height',
        type=int,
        default=None,
        help='PBF line height. Default: max(size, font metrics height).',
    )
    parser.add_argument(
        '--threshold',
        type=int,
        default=96,
        help='Antialias threshold from 0-255. Default: 96.',
    )
    parser.add_argument(
        '--no-ascii',
        action='store_true',
        help='Do not include ASCII 0x20-0x7E.',
    )
    parser.add_argument(
        '--output',
        required=True,
        help='Output .pbf path.',
    )
    parser.add_argument(
        '--preview',
        help='Optional text preview output path.',
    )
    return parser.parse_args()


def load_charset(paths, text_items, include_ascii):
    chars = set()

    if include_ascii:
        chars.update(chr(cp) for cp in range(0x20, 0x7F))

    chars.update(chr(cp) for cp in (
        0x3001, 0x3002, 0x300A, 0x300B, 0x300C, 0x300D,
        0x300E, 0x300F, 0x2014, 0x2026, 0x25AF, 0xFF01,
        0xFF08, 0xFF09, 0xFF0C, 0xFF1A, 0xFF1B, 0xFF1F,
    ))

    for item in text_items:
        chars.update(item)

    for path in paths:
        with open(path, 'r', encoding='utf-8') as fh:
            for line in fh:
                if line.startswith('#'):
                    continue
                chars.update(line.rstrip('\n\r'))

    chars.discard('\n')
    chars.discard('\r')
    chars.discard('\t')
    chars.add(chr(FALLBACK_CODEPOINT))

    return sorted(ord(ch) for ch in chars)


def clamp_int8(value, name, codepoint):
    if value < -128 or value > 127:
        raise ValueError(
            f'{name} for U+{codepoint:04X} is outside int8: {value}'
        )
    return value


def clamp_u8(value, name, codepoint):
    if value < 0 or value > 255:
        raise ValueError(
            f'{name} for U+{codepoint:04X} is outside uint8: {value}'
        )
    return value


def render_fallback(line_height):
    size = max(5, min(24, line_height - 2))
    bits = []
    for y in range(size):
        for x in range(size):
            bits.append(y == 0 or y == size - 1 or x == 0 or x == size - 1)

    return Glyph(
        width=size,
        height=size,
        left=1,
        top=max(0, (line_height - size) // 2),
        advance=size + 2,
        bits=bits,
    )


def render_glyph(font, codepoint, line_height, threshold):
    if codepoint == FALLBACK_CODEPOINT:
        return render_fallback(line_height)

    char = chr(codepoint)
    bbox = font.getbbox(char)
    advance = int(math.ceil(font.getlength(char)))

    if bbox is None:
        return None

    left, top, right, bottom = [int(math.floor(v)) for v in bbox]
    width = max(0, right - left)
    height = max(0, bottom - top)

    if width == 0 or height == 0:
        return Glyph(0, 0, 0, 0, max(advance, 0), [])

    image = Image.new('L', (width, height), 0)
    draw = ImageDraw.Draw(image)
    draw.text((-left, -top), char, font=font, fill=255)
    crop = image.getbbox()

    if crop is None:
        return Glyph(0, 0, 0, 0, max(advance, 0), [])

    crop_left, crop_top, crop_right, crop_bottom = crop
    image = image.crop(crop)
    left += crop_left
    top += crop_top
    width = crop_right - crop_left
    height = crop_bottom - crop_top

    pixels = image.tobytes()
    bits = [pixel >= threshold for pixel in pixels]

    return Glyph(
        width=clamp_u8(width, 'width', codepoint),
        height=clamp_u8(height, 'height', codepoint),
        left=clamp_int8(left, 'left', codepoint),
        top=clamp_int8(top, 'top', codepoint),
        advance=clamp_int8(max(advance, width + left), 'advance', codepoint),
        bits=bits,
    )


def build_glyphs(font, codepoints, line_height, threshold):
    glyphs = {}
    missing = []

    for codepoint in codepoints:
        glyph = render_glyph(font, codepoint, line_height, threshold)
        if glyph is None:
            missing.append(codepoint)
            continue
        glyphs[codepoint] = glyph

    glyphs[FALLBACK_CODEPOINT] = render_fallback(line_height)
    return glyphs, missing


def pack_bits(bits):
    padded = list(bits)
    while len(padded) % 32:
        padded.append(False)

    out = bytearray()
    for offset in range(0, len(padded), 8):
        byte = 0
        for bit in range(8):
            if padded[offset + bit]:
                byte |= 1 << bit
        out.append(byte)

    return bytes(out)


def write_pbf(path, glyphs, line_height):
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    if len(glyphs) > 0xFFFF:
        raise ValueError(
            f'PBF stores glyph count in uint16: {len(glyphs)} glyphs'
        )

    max_codepoint = max(glyphs.keys())
    codepoint_bytes = 2 if max_codepoint <= 0xFFFF else 4
    offset_entry_len = codepoint_bytes + 4

    glyph_blob = bytearray(struct.pack('<xxxx'))
    glyph_offsets = {}
    ordered = [FALLBACK_CODEPOINT]
    ordered.extend(
        cp for cp in sorted(glyphs.keys()) if cp != FALLBACK_CODEPOINT
    )

    for codepoint in ordered:
        glyph = glyphs[codepoint]
        glyph_offsets[codepoint] = len(glyph_blob)
        glyph_blob.extend(struct.pack(
            '<BBbbb',
            glyph.width,
            glyph.height,
            glyph.left,
            glyph.top,
            glyph.advance,
        ))
        glyph_blob.extend(pack_bits(glyph.bits))

    groups = {}
    for codepoint in sorted(glyphs.keys()):
        groups.setdefault(codepoint % HASH_TABLE_SIZE, []).append(codepoint)

    offset_blob = bytearray()
    offset_meta = {}
    for hash_value in sorted(groups.keys()):
        group = groups[hash_value]
        if len(group) > 255:
            raise ValueError(
                f'hash bucket {hash_value} has {len(group)} glyphs; '
                'PBF stores bucket sizes in uint8'
            )
        offset_meta[hash_value] = (len(offset_blob), len(group))
        for codepoint in group:
            if codepoint_bytes == 2:
                offset_blob.extend(struct.pack('<H', codepoint))
            else:
                offset_blob.extend(struct.pack('<L', codepoint))
            offset_blob.extend(struct.pack('<L', glyph_offsets[codepoint]))

    if len(offset_blob) > 0xFFFF:
        raise ValueError(
            'offset table is too large for the current PBF hash table '
            f'format: {len(offset_blob)} bytes'
        )

    hash_blob = bytearray()
    for hash_value in range(HASH_TABLE_SIZE):
        offset, size = offset_meta.get(hash_value, (0, 0))
        hash_blob.extend(struct.pack('<BBH', hash_value, size, offset))

    header = struct.pack(
        '<BBHHBBBB',
        3,
        line_height,
        len(glyphs),
        FALLBACK_CODEPOINT,
        HASH_TABLE_SIZE,
        codepoint_bytes,
        10,
        0,
    )

    with open(path, 'wb') as fh:
        fh.write(header)
        fh.write(hash_blob)
        fh.write(offset_blob)
        fh.write(glyph_blob)

    return len(header) + len(hash_blob) + len(offset_blob) + len(glyph_blob)


def write_preview(path, glyphs):
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    with open(path, 'w', encoding='utf-8') as fh:
        for codepoint in sorted(glyphs.keys()):
            glyph = glyphs[codepoint]
            label = chr(codepoint)
            print(f'glyph U+{codepoint:04X} {label}', file=fh)
            print(
                f'  size={glyph.width}x{glyph.height} '
                f'left={glyph.left} top={glyph.top} '
                f'advance={glyph.advance}',
                file=fh,
            )
            for y in range(glyph.height):
                row = glyph.bits[y * glyph.width:(y + 1) * glyph.width]
                print('  ' + ''.join('#' if bit else ' ' for bit in row),
                      file=fh)
            print('', file=fh)


def main():
    args = parse_args()
    source = Path(args.font)

    if not source.exists():
        raise SystemExit(f'font not found: {source}')

    font = ImageFont.truetype(
        str(source),
        args.size,
        index=args.font_index,
    )
    ascent, descent = font.getmetrics()
    line_height = args.line_height or max(args.size, ascent + descent)
    codepoints = load_charset(
        args.charset_file,
        args.text,
        include_ascii=not args.no_ascii,
    )
    glyphs, missing = build_glyphs(
        font,
        codepoints,
        line_height,
        args.threshold,
    )
    written = write_pbf(args.output, glyphs, line_height)

    if args.preview:
        write_preview(args.preview, glyphs)

    print(
        f'wrote {args.output}: {written} bytes, '
        f'{len(glyphs)} glyphs, line_height={line_height}'
    )
    if missing:
        print(
            'missing codepoints: ' +
            ', '.join(f'U+{cp:04X}' for cp in missing),
            file=sys.stderr,
        )


if __name__ == '__main__':
    main()
