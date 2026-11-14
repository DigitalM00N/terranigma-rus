"""Tiny dependency-free PNG reader/writer for indexed/grayscale images."""
import struct
import zlib


def write_png(path, width, height, pixels, palette):
    """pixels: list of rows (lists of palette indices). palette: list of (r,g,b)."""
    raw = bytearray()
    for row in pixels:
        raw.append(0)
        raw.extend(row)

    def chunk(t, data):
        c = struct.pack(">I", len(data)) + t + data
        return c + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)

    out = b"\x89PNG\r\n\x1a\n"
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0))
    out += chunk(b"PLTE", b"".join(bytes(c) for c in palette))
    out += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    out += chunk(b"IEND", b"")
    open(path, "wb").write(out)


def read_png(path):
    """Read an 8-bit indexed (color type 3) or RGB/RGBA/gray PNG.
    Returns (width, height, rows) where rows hold palette indices for indexed PNGs,
    otherwise (r,g,b) tuples."""
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos = 8
    idat = b""
    plte = None
    while pos < len(data):
        ln, = struct.unpack(">I", data[pos:pos + 4])
        t = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if t == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif t == b"PLTE":
            plte = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        elif t == b"IDAT":
            idat += body
    assert depth == 8 and interlace == 0, "only 8-bit non-interlaced PNG supported"
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(idat)
    stride = w * bpp
    rows = []
    prev = bytearray(stride)
    p = 0
    for _ in range(h):
        f = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 0xFF
            elif f == 2:
                line[i] = (line[i] + b) & 0xFF
            elif f == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 0xFF
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        rows.append(line)
        prev = line
    if ctype == 3:
        return w, h, [list(r) for r in rows], plte
    out = []
    for r in rows:
        if ctype == 0:
            out.append([(v, v, v) for v in r])
        elif ctype == 4:
            out.append([(r[i], r[i], r[i]) for i in range(0, len(r), 2)])
        else:
            out.append([tuple(r[i:i + 3]) for i in range(0, len(r), bpp)])
    return w, h, out, None
