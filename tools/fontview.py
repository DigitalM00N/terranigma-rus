"""Render 16x16 2bpp glyph sheets from the ROM to PNG (inspection helper)."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from png import write_png

def glyph(d, off):
    g = [[0]*16 for _ in range(16)]
    for ti, (tx, ty) in enumerate([(0,0),(8,0),(0,8),(8,8)]):
        o = off + ti*16
        for y in range(8):
            lo, hi = d[o+2*y], d[o+2*y+1]
            for x in range(8):
                g[ty+y][tx+x] = ((lo>>(7-x))&1) | (((hi>>(7-x))&1)<<1)
    return g

def sheet(d, base, n, path, S=2, cols=16):
    W = cols*18*S; H = ((n+cols-1)//cols)*18*S
    img = [[4]*W for _ in range(H)]
    for c in range(n):
        g = glyph(d, base+c*64)
        gx = (c%cols)*18*S; gy = (c//cols)*18*S
        for y in range(16):
            for x in range(16):
                for a in range(S):
                    for b in range(S):
                        img[gy+y*S+a][gx+x*S+b] = g[y][x]
    write_png(path, W, H, img, [(255,255,255),(0,0,0),(120,120,200),(200,80,80),(230,230,160)])

if __name__ == "__main__":
    d = open(sys.argv[1], "rb").read()
    sheet(d, int(sys.argv[2], 16), int(sys.argv[3]), sys.argv[4])
