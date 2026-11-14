"""Read Mesen 2 savestates (.mss): screenshot, WRAM, VRAM, CGRAM."""
import struct
import zlib


def load(path):
    d = open(path, "rb").read()
    blobs = []
    i = 0
    while i < len(d) - 2:
        if d[i] == 0x78 and d[i + 1] in (0x01, 0x5E, 0x9C, 0xDA):
            try:
                z = zlib.decompressobj()
                o = z.decompress(d[i:])
                blobs.append(o)
                i += len(d[i:]) - len(z.unused_data)
                continue
            except zlib.error:
                pass
        i += 1
    shot, state = blobs[0], blobs[1]
    return shot, state


def field(state, name, size):
    k = state.find(name.encode())
    j = state.find(size.to_bytes(4, "little"), k)
    return state[j + 4:j + 4 + size]


def screenshot_rows(shot):
    W = 256
    H = len(shot) // 2 // W
    rows = []
    for y in range(H):
        rows.append([shot[(y * W + x) * 2] | shot[(y * W + x) * 2 + 1] << 8 for x in range(W)])
    return rows
