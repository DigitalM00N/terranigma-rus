"""Quintet LZ (decompressor at $86:83BE) - decompress and compress."""


def decompress(rom, off, max_out=0x20000):
    chunks = rom[off]
    p = off + 3
    out = bytearray()
    while True:
        y = 0
        bits = 1
        flags = 0

        def nextbit():
            nonlocal bits, flags, y
            bits -= 1
            if bits == 0:
                bits = 8
                flags = rom[p + y]
                y += 1
            flags <<= 1
            return (flags >> 8) & 1

        out.append(rom[p + y])
        y += 1
        while True:
            if nextbit():
                out.append(rom[p + y])
                y += 1
                continue
            if nextbit():
                v = (rom[p + y] << 8) | rom[p + y + 1]
                y += 2
                offset = ((v >> 3) | 0xE000) - 0x10000
                n = v & 7
                if n == 0:
                    # extra length byte: copies n+1 bytes (the 3-bit form copies n+2)
                    n = rom[p + y]
                    y += 1
                    if n == 0:
                        break
                    n -= 1
            else:
                n = (nextbit() << 1) | nextbit()
                offset = rom[p + y] - 0x100
                y += 1
            for _ in range(n + 2):
                out.append(out[offset])
            if len(out) > max_out:
                raise ValueError("runaway")
        chunks = (chunks - 1) & 0xFF
        if chunks == 0 or chunks == 0xFF:
            return bytes(out), p + y - off
        p = p + y + 2


def compress(data):
    """Optimal-parse LZ in the same format (single chunk). Returns bytes incl. 3-byte header.
    Costs in bits: literal 9, short ref 12 (len 2-5, dist <= 256), long ref 18 (len 3-9),
    long ref with length byte 26 (len 2-256)."""
    n = len(data)
    if n == 0:
        return bytes([1, 0, 0, 0, 0x40, 0, 0, 0])
    # longest matches
    best_any = [(0, 0)] * n      # (len, dist)
    best_short = [(0, 0)] * n
    chains = {}
    for i in range(n):
        # short window
        bl, bd = 0, 0
        for dist in range(1, min(256, i) + 1):
            j = i - dist
            k = 0
            while k < 5 and i + k < n and data[j + k] == data[i + k]:
                k += 1
            if k > bl:
                bl, bd = k, dist
                if k == 5:
                    break
        best_short[i] = (bl, bd)
        # long window via 3-byte hash chains
        al, ad = bl, bd
        if i + 2 < n:
            key = data[i:i + 3]
            cand = chains.get(key)
            if cand:
                tried = 0
                for j in reversed(cand):
                    dist = i - j
                    if dist > 0x2000:
                        break
                    k = 3
                    while k < 256 and i + k < n and data[j + k] == data[i + k]:
                        k += 1
                    if k > al:
                        al, ad = k, dist
                        if k == 256:
                            break
                    tried += 1
                    if tried > 512:
                        break
            chains.setdefault(key, []).append(i)
        best_any[i] = (al, ad)
    INF = 1 << 60
    cost = [INF] * (n + 1)
    choice = [None] * (n + 1)
    cost[1] = 0
    for i in range(1, n):
        c = cost[i]
        if c == INF:
            continue
        if c + 9 < cost[i + 1]:
            cost[i + 1] = c + 9
            choice[i + 1] = ("lit", i)
        sl, sd = best_short[i]
        for L in range(2, sl + 1):
            if c + 12 < cost[i + L]:
                cost[i + L] = c + 12
                choice[i + L] = ("short", i, L, sd)
        al, ad = best_any[i]
        for L in range(2, al + 1):
            if L >= 3 and L <= 9:
                v = c + 18
                kind = "long"
            else:
                v = c + 26
                kind = "ext"
            if v < cost[i + L]:
                cost[i + L] = v
                choice[i + L] = (kind, i, L, ad)
    ops = []
    p = n
    while p > 1:
        ch = choice[p]
        ops.append(ch)
        p = ch[1]
    ops.reverse()

    stream = bytearray([data[0]])
    flags_pos = None
    nbits = 0

    def putbit(b):
        nonlocal flags_pos, nbits
        if nbits == 0:
            flags_pos = len(stream)
            stream.append(0)
            nbits = 8
        nbits -= 1
        if b:
            stream[flags_pos] |= 1 << nbits

    for op in ops:
        if op[0] == "lit":
            putbit(1)
            stream.append(data[op[1]])
        elif op[0] == "short":
            _, i, L, dist = op
            putbit(0); putbit(0)
            putbit((L - 2) >> 1); putbit((L - 2) & 1)
            stream.append(0x100 - dist)
        else:
            _, i, L, dist = op
            putbit(0); putbit(1)
            off13 = (0x2000 - dist) & 0x1FFF
            if op[0] == "long":
                v = (off13 << 3) | (L - 2)
                stream += bytes([v >> 8, v & 0xFF])
            else:
                v = off13 << 3
                stream += bytes([v >> 8, v & 0xFF, L - 1])
    putbit(0); putbit(1)
    stream += bytes([0, 0, 0])
    return bytes([1, 0, 0]) + bytes(stream)


def compress_greedy(data):
    """Greedy LZ in the same format (single chunk). Returns bytes incl. 3-byte header."""
    out = bytearray([1, 0, 0])
    flags_pos = None
    nbits = 0
    stream = bytearray()

    def putbit(b):
        nonlocal flags_pos, nbits
        if nbits == 0:
            flags_pos = len(stream)
            stream.append(0)
            nbits = 8
        nbits -= 1
        if b:
            stream[flags_pos] |= 1 << nbits

    stream.append(data[0])
    i = 1
    n = len(data)
    while i < n:
        best_len, best_off = 0, 0
        lo = max(0, i - 0x1FFF)
        for j in range(i - 1, lo - 1, -1):
            k = 0
            while i + k < n and data[j + k] == data[i + k] and k < 256:
                k += 1
            if k > best_len:
                best_len, best_off = k, i - j
                if k >= 256:
                    break
        if best_len >= 2 and best_off <= 0x100 and best_len <= 5:
            putbit(0); putbit(0)
            L = best_len - 2
            putbit(L >> 1); putbit(L & 1)
            stream.append(0x100 - best_off)
            i += best_len
        elif best_len >= 3:
            putbit(0); putbit(1)
            off13 = (0x2000 - best_off) & 0x1FFF
            if best_len <= 9:
                v = (off13 << 3) | (best_len - 2)
                stream += bytes([v >> 8, v & 0xFF])
            else:
                v = off13 << 3
                stream += bytes([v >> 8, v & 0xFF, best_len - 1])
            i += best_len
        else:
            putbit(1)
            stream.append(data[i])
            i += 1
    # end marker: long ref with n=0, extra 0
    putbit(0); putbit(1)
    stream += bytes([0, 0, 0])
    return bytes(out + stream)
