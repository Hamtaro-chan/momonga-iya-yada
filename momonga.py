#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
momonga.py - Single-Source-of-Truth 3D ASCII Animation Engine (Pure Python)
  - Zero external dependencies; runs in Terminal (`python momonga.py`)
    or in the browser via Pyodide (`python momonga.py --web`).
"""
import os
import sys
import time
import math
import shutil

if os.name == 'nt':
    os.system('')

ESC = chr(27)
HIDE_CURSOR, SHOW_CURSOR = ESC + "[?25l", ESC + "[?25h"
HOME, RESET = ESC + "[H", ESC + "[0m"

def _ansi_rgb(r, g, b):
    return "%s[38;2;%d;%d;%dm" % (ESC, r, g, b)

# Shared RGB Palette (0=BG, 1-4=Body, 5-8=Tail, 9=Nose, 10=Blush, 11=Slash, 12=EyeHi, 13-16=Kana, 17-21=FX)
PALETTE_RGB = (
    (5, 5, 7), (150, 142, 126), (253, 251, 244), (235, 229, 213), (194, 186, 166),
    (88, 144, 176), (196, 242, 255), (142, 218, 255), (104, 180, 220),
    (158, 232, 255), (245, 148, 170), (74, 44, 32), (255, 255, 255),
    (255, 247, 250), (232, 213, 220), (235, 138, 166), (168, 96, 118),
    (58, 56, 50), (110, 106, 96), (140, 133, 120), (214, 209, 196), (140, 134, 122),
)
PALETTE_HEX = ["#%02x%02x%02x" % rgb for rgb in PALETTE_RGB]
PALETTE_ANSI = [_ansi_rgb(*rgb) for rgb in PALETTE_RGB]
C_HUD, C_HUD_DIM = _ansi_rgb(212, 212, 216), _ansi_rgb(148, 152, 164)

BODY_RAMP_BYTES = b"na0QOB86#$S51000"
TAIL_RAMP_BYTES = b"no0QB86#$S500"

# Deduplicated Kana Bitmaps for 「いややだやだ」
_GLYPH_MAP = {
    'い': ("  #8x.    #8. ", " 8000#    #08x", " #000#     #00", " #000#     .80", " +800#  ..  #0", "  #008.+8#  ..", "   x8000#     ", "    .+##.     "),
    'や': ("    .#8.  #8x ", "   .#00.  .#0x", " .#80000###00#", " #008.#008.800", "  ..  #000.#00", "     .#008x80#", "      #000.   ", "      .#8x    "),
    'だ': ("   .#8x   #x# ", " #800000#.#x# ", "   #008.      ", "  .#000. #800x", "  .#008.   .. ", "  #000. +8.   ", "  #008. #0000#", "  .#8.   x800#"),
}
KANA_GLYPHS = tuple(_GLYPH_MAP[ch] for ch in "いややだやだ")

# Shared inner fur strokes & outer angles for the fluffy side/back tail
_INNER_FUR = (
    (-1, -24,  5, -20, 11, -14), (2, -16,  9, -12, 15, -6),
    ( 5,  -7, 12,  -3, 18,   3), (6,   3, 13,   7, 19, 13),
    ( 5,  12, 12,  16, 18,  22), (3,  20,  9,  24, 15, 29),
)
_OUTER_ANGLES_R = (-1.65, -1.22, -0.80, -0.38, 0.04, 0.44, 0.84, 1.24, 1.62)
_OUTER_ANGLES_L = ( 1.55,  1.98,  2.40,  2.82, 3.24, 3.64, 4.04, 4.44, 4.82)

# Front-dance 4-beat keyframes: (tilt, shiftX, lLift, rLift, lCurl, rState, tRx, tRy, tCx, tCy, tTx, tTy)
_FRONT_KF = (
    ( 0.11,  4.5, 1.0, 0.0, 1.0,  0.0, -10, 20, -38,   6, -30, -26),
    (-0.11, -4.5, 0.0, 1.0, 0.0, -1.0,  10, 20,  38,   6,  30, -26),
    (-0.14, -2.5, 0.0, 1.0, 1.0,  1.0,   4, 12,  14, -22,  -2, -54),
    ( 0.08,  2.5, 1.0, 0.0, 0.2,  0.0,  -8, 18, -32,  -4, -24, -36),
)


def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def lerp(a, b, t):
    return a + (b - a) * t


def smoothstep(e0, e1, x):
    t = clamp((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def bezier_pt(p0, p1, p2, t):
    u = 1.0 - t
    return u * u * p0 + 2.0 * u * t * p1 + t * t * p2


def bezier_tan(p0, p1, p2, t):
    return 2.0 * (1.0 - t) * (p1 - p0) + 2.0 * t * (p2 - p1)


class VectorMaskCanvas:
    """Fast scanline 2D rasterizer using C-level bytearray slice fills."""
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.buf = bytearray(w * h)
        self._zero = bytes(w * h)
        self._fills = [bytes([m]) * w for m in range(7)]
        self.tx = self.ty = self.rot = self.sr = 0.0
        self.sx = self.sy = self.cr = 1.0
        self.stack = []

    def clear(self):
        self.buf[:] = self._zero

    def save(self):
        self.stack.append((self.tx, self.ty, self.rot, self.sx, self.sy, self.cr, self.sr))

    def restore(self):
        self.tx, self.ty, self.rot, self.sx, self.sy, self.cr, self.sr = self.stack.pop()

    def set_pose(self, cx, cy, rot, squash):
        self.tx, self.ty, self.rot = cx, cy, rot
        self.sx, self.sy = squash, 1.0 / squash
        self.cr, self.sr = math.cos(rot), math.sin(rot)

    def _to_screen(self, x, y):
        xs, ys = x * self.sx, y * self.sy
        return (self.tx + xs * self.cr - ys * self.sr,
                self.ty + xs * self.sr + ys * self.cr)

    def fill_ellipse(self, cx, cy, rx, ry, erot, mat):
        scx, scy = self._to_screen(cx, cy)
        ca, sa = math.cos(-(self.rot + erot)), math.sin(-(self.rot + erot))
        rx_s, ry_s = max(0.8, rx * self.sx), max(0.8, ry * self.sy)
        irx2, iry2 = 1.0 / (rx_s * rx_s), 1.0 / (ry_s * ry_s)
        A = ca * ca * irx2 + sa * sa * iry2
        inv_2A = 0.5 / A
        b_coef = 2.0 * ca * sa * (iry2 - irx2)
        c_coef = sa * sa * irx2 + ca * ca * iry2
        max_ry = math.sqrt(rx_s * rx_s * sa * sa + ry_s * ry_s * ca * ca) + 1.0

        w, buf, fill, sqrt = self.w, self.buf, self._fills[mat], math.sqrt
        for py in range(max(0, int(scy - max_ry)), min(self.h - 1, int(scy + max_ry)) + 1):
            dy = py - scy
            B = b_coef * dy
            D = B * B - 4.0 * A * (c_coef * dy * dy - 1.0)
            if D >= 0.0:
                sq = sqrt(D)
                x0 = max(0, int(math.ceil(scx + (-B - sq) * inv_2A)))
                x1 = min(w - 1, int(math.floor(scx + (-B + sq) * inv_2A)))
                if x0 <= x1:
                    off = py * w
                    buf[off + x0 : off + x1 + 1] = fill[: x1 - x0 + 1]

    def stroke_line(self, x0, y0, x1, y1, width, mat):
        sx0, sy0 = self._to_screen(x0, y0)
        sx1, sy1 = self._to_screen(x1, y1)
        rad = max(0.6, width * 0.5)
        steps = max(2, int(math.hypot(sx1 - sx0, sy1 - sy0) / max(1.0, rad * 0.65)))
        r2, ir = rad * rad, int(rad + 0.5)
        w, h, buf, fill, sqrt = self.w, self.h, self.buf, self._fills[mat], math.sqrt

        for i in range(steps + 1):
            u = i / steps
            px, py = sx0 + (sx1 - sx0) * u, sy0 + (sy1 - sy0) * u
            for yy in range(max(0, int(py - ir)), min(h - 1, int(py + ir)) + 1):
                rem = r2 - (yy - py) ** 2
                if rem >= 0.0:
                    dx = sqrt(rem)
                    lx0, lx1 = max(0, int(px - dx)), min(w - 1, int(px + dx))
                    if lx0 <= lx1:
                        off = yy * w
                        buf[off + lx0 : off + lx1 + 1] = fill[: lx1 - lx0 + 1]

    def stroke_curve(self, x0, y0, cx1, cy1, x1, y1, width, mat):
        px, py = x0, y0
        for i in range(1, 9):
            t = i / 8.0
            nx, ny = bezier_pt(x0, cx1, x1, t), bezier_pt(y0, cy1, y1, t)
            self.stroke_line(px, py, nx, ny, width, mat)
            px, py = nx, ny

    def fill_tuft(self, bx0, by0, tx1, ty1, bx2, by2, mat):
        for i in range(5):
            u = i / 4.0
            self.stroke_line(lerp(bx0, bx2, u), lerp(by0, by2, u), tx1, ty1, 3.6, mat)


def _draw_plume_tail(mc, rx, ry, cx, cy, tx, ty, base_r=25.5):
    """Bezier-curved sky-blue tail used behind Momonga in Scene 1."""
    for i in range(15):
        t = i / 14.0
        r = base_r * (0.52 + 0.58 * math.sin(t * math.pi * 0.82))
        mc.fill_ellipse(bezier_pt(rx, cx, tx, t), bezier_pt(ry, cy, ty, t), r, r * 0.96, 0.0, 2)

    for side in (-1, 1):
        for k in range(6):
            t = 0.14 + (k / 5.0) * 0.78
            sx, sy = bezier_pt(rx, cx, tx, t), bezier_pt(ry, cy, ty, t)
            dx, dy = bezier_tan(rx, cx, tx, t), bezier_tan(ry, cy, ty, t)
            ln = math.hypot(dx, dy) or 1.0
            ux, uy = dx / ln, dy / ln
            nx, ny = side * -uy, side * ux
            rl = base_r * (0.52 + 0.58 * math.sin(t * math.pi * 0.82))
            bx, by = sx + nx * (rl * 0.76), sy + ny * (rl * 0.76)
            mc.fill_tuft(bx - ux * 7.5, by - uy * 7.5,
                         sx + nx * (rl + 9.5) + ux * 4.5, sy + ny * (rl + 9.5) + uy * 4.5,
                         bx + ux * 7.5, by + uy * 7.5, 2)
            lx0, ly0 = sx + nx * (rl * 0.36) - ux * 2.0, sy + ny * (rl * 0.36) - uy * 2.0
            lx1, ly1 = sx + nx * (rl + 6.5) + ux * 2.5, sy + ny * (rl + 6.5) + uy * 2.5
            mc.stroke_curve(lx0, ly0, (lx0 + lx1) * 0.5 + ux * 4.8, (ly0 + ly1) * 0.5 + uy * 4.8, lx1, ly1, 2.8, 0)


def _draw_fluffy_tail(mc, tcx, tcy, trot, shift_x, shift_y, side=1):
    """Unified side/back fluffy sky-blue tail (side=+1 right, side=-1 left)."""
    mc.fill_ellipse(tcx, tcy, 24.5, 30.5, trot, 2)
    mc.fill_ellipse(tcx + side * 4.0, tcy - (6.0 if side > 0 else 5.0), 22.0, 25.0, trot - side * 0.1, 2)
    if side > 0:
        mc.fill_ellipse(tcx - 4.0, tcy + 10.0, 19.0, 20.0, trot + 0.15, 2)

    for x0, y0, cx, cy, x1, y1 in _INNER_FUR:
        mc.stroke_curve(side * x0 + shift_x, y0 + shift_y,
                        side * cx + shift_x, cy + shift_y,
                        side * x1 + shift_x, y1 + shift_y, 3.1, 0)

    for a0 in (_OUTER_ANGLES_R if side > 0 else _OUTER_ANGLES_L):
        ang = a0 + trot * 0.65
        ca, sa = math.cos(ang), math.sin(ang)
        rx, ry = tcx + ca * 22.0, tcy + sa * 28.0
        mc.fill_tuft(rx - sa * 7.5, ry + ca * 7.5,
                     tcx + ca * 31.5 - side * sa * 3.0, tcy + sa * 36.5 + side * ca * 3.0,
                     rx + sa * 7.5, ry - ca * 7.5, 2)
        ix, iy = tcx + ca * 14.5 - side * sa * 2.0, tcy + sa * 19.5 + side * ca * 2.0
        ox, oy = tcx + ca * 29.0 + side * sa * 2.0, tcy + sa * 34.0 - side * ca * 2.0
        mc.stroke_curve(ix, iy, (ix + ox) * 0.5 - side * sa * 3.8, (iy + oy) * 0.5 + side * ca * 3.8, ox, oy, 3.1, 0)


def draw_momonga_front_pose(mc, p):
    """Scene 1: Front alternating-leg dance with waving arms and sweeping tail."""
    mc.save()
    mc.set_pose(mc.w * 0.54 + p['shiftX'], mc.h * 0.54 - p['hopY'] * 22.0, p['tilt'], p['squash'])

    _draw_plume_tail(mc, p['tRx'], p['tRy'], p['tCx'], p['tCy'], p['tTx'], p['tTy'])

    # Black separation halo + ears (mirrored left/right)
    mc.fill_ellipse(0, -5, 47.0, 38.0, 0.0, 0)
    mc.fill_ellipse(0, 17, 42.0, 32.5, 0.0, 0)
    ear_wig = math.sin(p['wagPhase']) * 0.04
    for s in (-1, 1):
        rot = s * (0.20 - ear_wig)
        mc.fill_ellipse(s * 25, -42, 13.5, 17.5, rot, 0)
        mc.fill_ellipse(s * 25, -42, 10.8, 14.8, rot, 1)
        mc.fill_ellipse(s * 24, -41,  6.2,  9.5, rot, 3)

    # White head & torso + ear creases + 3 cheek tufts per side
    mc.fill_ellipse(0, -5, 44.2, 35.2, 0.0, 1)
    mc.fill_ellipse(0, 17, 39.2, 29.8, 0.0, 1)
    for s in (-1, 1):
        mc.stroke_line(s * 20, -30, s * 23, -39, 2.7, 0)
        mc.stroke_line(s * 20, -32, s * 27, -35, 2.5, 0)
        for i, ty in enumerate((-5, 2, 9)):
            dy = (i - 1) * 2.6
            mc.stroke_line(s * 40, ty,       s * 53, ty + dy,       6.4, 1)
            mc.stroke_line(s * 41, ty - 3.3, s * 53, ty + dy - 3.3, 2.4, 0)

    # Alternating legs
    for s, lift in ((-1, p['lLift']), (1, p['rLift'])):
        if lift < 0.55:
            mc.stroke_line(s * 9, 41, s * 9, lerp(55.0, 46.0, lift / 0.55), 12.0, 1)
        else:
            ay = lerp(45.0, 40.0, (lift - 0.55) / 0.45)
            mc.fill_ellipse(s * 12, ay,       11.0, 12.0, -s * 0.12, 0)
            mc.fill_ellipse(s * 12, ay + 1.0,  8.4,  9.5, -s * 0.12, 1)

    # Arms (left curl + right curl/wave)
    lc = p['lCurl']
    la = (lerp(-34, -24, lc), lerp(16, 22, lc), lerp(-41, -12, lc), lerp(21, 15, lc), lerp(-47, -6, lc), lerp(26, 21, lc))
    rs = p['rState']
    u = abs(rs)
    ra = (lerp(34, 24, u), lerp(16, 20, u), lerp(41, 12, u), lerp(21, 14, u), lerp(47, 6, u), lerp(26, 20, u)) if rs < 0 else \
         (lerp(34, 35, u), lerp(16, -3, u), lerp(41, 45, u), lerp(21, -12, u), lerp(47, 53, u), lerp(26, -21, u))
    for arm in (la, ra):
        mc.stroke_curve(*arm, 12.2, 0)
        mc.stroke_curve(*arm,  8.2, 1)

    # Smug face: half-lidded eyes, brows, blue nose, ω mouth, rosy blush
    for s in (-1, 1):
        mc.fill_ellipse(s * 17, -5.0, 9.8, 7.5, s * 0.08, 0)
        mc.fill_ellipse(s * 17, -6.6, 6.8, 4.0, s * 0.08, 5)
        mc.stroke_line(s * 10.5, -15, s * 7.2, -13.2, 2.5, 0)
        mc.fill_ellipse(s * 28, 3.8, 9.8, 5.8, s * 0.1, 4)
        for k in (-1, 0, 1):
            mc.stroke_line(s * 28 + k * 3.7 - 1, 5.8, s * 28 + k * 3.7 + 1, 1.8, 2.0, 6)
    mc.fill_ellipse(0, -5, 5.4, 4.4, 0.0, 3)
    mc.stroke_curve(-5.8, 2.5, -2.8, 7.0, 0, 2.2, 2.7, 0)
    mc.stroke_curve( 0.0, 2.2,  2.8, 7.0, 5.8, 2.5, 2.7, 0)
    mc.stroke_line(-2.5, 9.5, 2.5, 9.5, 2.4, 0)
    mc.restore()


def draw_momonga_tail_wag(mc, p):
    """Scene 2: Continuous 2-sided tail wag (side-profile right <-> back-view left)."""
    s = p['tailSwing']
    step = math.sin(p['wagPhase'] * 2.0)
    mc.save()

    if s >= -0.18:
        whip = 1.0 - smoothstep(-0.12, 0.65, s)
        mc.set_pose(mc.w * 0.50 - whip * 4.0, mc.h * 0.54 - p['hopY'] * 18.0 + whip * 2.5, -0.06 - whip * 0.14, p['squash'])

        mc.stroke_line(-6, 33, -11, 50 - max(0.0, step) * 3.0, 9.5, 1)
        mc.stroke_line( 6, 33,   1, 50 - max(0.0, -step) * 3.0, 9.5, 1)
        mc.stroke_line(-1, 35,  -5, 51, 3.0, 0)

        mc.fill_ellipse(2.0 - whip * 3.0, -36.0 + whip * 2.0, 8.5, 12.0, 0.48, 1)
        mc.stroke_curve(-11, -33, -3, -33, 5, -28, 3.0, 0)
        mc.fill_ellipse(-15, -6, 30.5, 27.5, -0.16, 1)
        mc.fill_ellipse(-27,  2, 18.5, 15.0, -0.08, 1)
        mc.fill_ellipse( -6, 15, 28.5, 23.5, -0.14, 1)
        mc.fill_ellipse(-19, -37, 9.2, 13.8, 0.14, 1)
        mc.stroke_curve(-11, -36, -13, -31, -10, -27, 3.0, 0)

        _draw_fluffy_tail(mc, lerp(24.0, 11.0, whip), lerp(2.0, -7.0, whip), lerp(0.18, -0.32, whip),
                          -whip * 13.0, -whip * 7.0, side=1)

        # Snout, mouth, vertical eye, blush, 4 whiskers, curled paw
        mc.fill_ellipse(-44.5, -0.5, 7.8, 5.8, -0.08, 1)
        mc.stroke_curve(-49.5, 0.2, -44.2, 5.2 + max(0.0, step) * 2.2, -39.2, 0.4, 3.0, 0)
        mc.fill_ellipse(-34.0, -8.5, 6.4, 9.6, -0.08, 0)
        mc.fill_ellipse(-34.8, -10.6, 4.0, 4.8, -0.08, 5)
        mc.stroke_line(-36.5, -21.0, -33.5, -19.8, 2.8, 0)
        mc.fill_ellipse(-25.0, 2.8, 9.2, 5.5, 0.0, 4)
        for k in (-1.5, -0.5, 0.5, 1.5):
            mc.stroke_line(-25.0 + k * 2.8 - 1.2, 4.8, -25.0 + k * 2.8 + 1.2, 0.8, 2.0, 6)
        for w in ((-12.5, -8.2, -6.0, -10.2, 1.0, -11.5), (-11.5, -2.8, -5.0, -3.5, 2.0, -3.6),
                  (-11.5,  2.8, -5.0,   3.5, 2.0,   4.8), (-10.5,  8.2, -5.0,  9.8, 0.5, 12.0)):
            mc.stroke_curve(*w, 3.1, 0)
        mc.fill_ellipse(-24.5, 18.5, 8.6, 6.4, -0.20, 1)
        mc.stroke_curve(-18.5, 13.5, -28.5, 18.5, -17.5, 23.8, 3.1, 0)
    else:
        whip = smoothstep(-0.95, -0.22, s)
        mc.set_pose(mc.w * 0.55 + whip * 5.0, mc.h * 0.54 - p['hopY'] * 18.0, whip * 0.20, p['squash'])

        mc.stroke_line(-9, 34, -10 + whip * 3, 50 - max(0.0, -step) * 3.5, 10.0, 1)
        mc.stroke_line( 8, 34,   7 + whip * 2, 50 - max(0.0,  step) * 3.5, 10.0, 1)
        mc.stroke_line(-1, 36,  -1, 51, 3.0, 0)
        mc.fill_ellipse(-16, -39, 9.2, 13.5, -0.12, 1)
        mc.fill_ellipse( 19, -38, 9.2, 13.5,  0.20, 1)
        mc.fill_ellipse(2, -6, 36.5, 30.0, 0.0, 1)
        mc.fill_ellipse(2, 15, 34.0, 25.5, 0.0, 1)
        for i, wy in enumerate((-9, -3, 3, 9)):
            dy = (i - 1.5) * 2.2
            mc.stroke_line(34, wy,       48, wy + dy,       6.2, 1)
            mc.stroke_line(35, wy - 2.6, 48, wy + dy - 2.6, 2.8, 0)
        mc.fill_ellipse(34, 19, 8.5, 6.4, 0.20, 1)
        mc.stroke_curve(28, 14, 38, 19, 28, 24, 3.1, 0)

        _draw_fluffy_tail(mc, lerp(-24.0, -12.0, whip), lerp(4.0, -5.0, whip), lerp(-0.20, 0.25, whip),
                          whip * 12.0, -whip * 6.0, side=-1)
    mc.restore()


def evaluate_timeline(t, mode='auto', bounce=1.0):
    """Choreography controller for Scene 1 (Front Dance) & Scene 2 (Iyayada Tail Wag)."""
    if mode == 'lathe_profile':
        prog = 0.28 + 0.72 * ((math.sin(t * 1.4 - math.pi * 0.5) + 1.0) * 0.5)
        return dict(view='front', phaseName='puri 0', sceneLabel='Body Profile Lathe Mode (3D Scanline Build-up)',
                    activeFn='def render_frame_grid()', rotDeg=336.50 + prog * 20.95,
                    latheCutoffRow=int(V_ROWS * (1.0 - prog * 0.85)), visibleKanaCount=0, activeKanaIdx=-1,
                    shiftX=0.0, hopY=0.0, squash=1.0, tilt=0.0, lLift=0.0, rLift=0.0, lCurl=1.0, rState=0.0,
                    tRx=-10.0, tRy=20.0, tCx=-36.0, tCy=6.0, tTx=-28.0, tTy=-26.0,
                    tailSwing=-0.8, wagPhase=t * 4.0, showMotionLines=False)

    loop_t = t % 13.35
    in_scene2 = (mode == 'scene2_iyayada') or (mode == 'auto' and loop_t >= 2.15)
    if not in_scene2:
        cpos = (t * 2.8) % 4.0
        b = int(cpos)
        sub = cpos - b
        ease, arc = 0.5 - 0.5 * math.cos(sub * math.pi), math.sin(sub * math.pi)
        k0, k1 = _FRONT_KF[b], _FRONT_KF[(b + 1) % 4]
        v = [lerp(a, c, ease) for a, c in zip(k0, k1)]
        tilt = (v[0] + 0.04 * arc * (1 if k0[0] >= 0 else -1)) * bounce
        return dict(view='front', phaseName='yah-ha %d' % b,
                    sceneLabel='Scene 1 (0.0s-2.15s): Front Alternating-Leg Dance (No Text)',
                    activeFn='def draw_momonga_front_pose()', rotDeg=(360.0 + tilt * 65.0) % 360.0,
                    latheCutoffRow=0, visibleKanaCount=0, activeKanaIdx=-1, shiftX=v[1],
                    hopY=(arc * 0.42 - 0.06) * bounce, squash=1.0 + (0.45 - arc) * 0.08 * bounce, tilt=tilt,
                    lLift=v[2] * (0.35 + 0.65 * arc), rLift=v[3] * (0.35 + 0.65 * arc), lCurl=v[4], rState=v[5],
                    tRx=v[6], tRy=v[7], tCx=v[8], tCy=v[9], tTx=v[10], tTy=v[11],
                    tailSwing=0.0, wagPhase=t * 7.2, showMotionLines=False)

    s2_t = (t % 11.20) if mode == 'scene2_iyayada' else (loop_t - 2.15)
    vis = sum(s2_t >= th for th in (0.02, 0.35, 0.68, 0.98, 1.28, 1.58))
    raw_sin = math.sin(s2_t * 5.4 - 0.8)
    swing = math.copysign(abs(raw_sin) ** 0.78, raw_sin)
    step_b = abs(math.cos(s2_t * 5.4 - 0.8))
    return dict(view='iyayada', phaseName='iyayada L' if swing < 0 else 'iyayada R',
                sceneLabel='Scene 2: 2-Sided Tail Wag 「いややだやだ」 · Text %d/6' % vis,
                activeFn='def draw_momonga_tail_wag()', rotDeg=180.0 + (swing + 1.0) * 45.0,
                latheCutoffRow=0, visibleKanaCount=vis, activeKanaIdx=min(5, max(0, vis - 1)),
                shiftX=0.0, hopY=step_b * 0.26 * bounce, squash=1.0 + (0.5 - step_b) * 0.06 * bounce,
                tilt=0.0, tailSwing=swing, wagPhase=s2_t * 5.4, showMotionLines=True)


V_COLS, V_ROWS, SS = 124, 76, 2
MASK = VectorMaskCanvas(V_COLS * SS, V_ROWS * SS)
_MAT_PRIO = (4, 0, 1, 2, 3, 5, 6)  # 6(Slash) > 5(EyeHi) > 0(Black Cutout) > 4 > 3 > 2 > 1


def render_frame_grid(t, out_cols=V_COLS, out_rows=V_ROWS, mode='auto', bounce=1.0):
    """Renders Momonga into `(chars_bytes, colors_bytes, meta_dict)` for Terminal or Web."""
    state = evaluate_timeline(t, mode, bounce)
    MASK.clear()
    if state['view'] == 'front':
        draw_momonga_front_pose(MASK, state)
    else:
        draw_momonga_tail_wag(MASK, state)

    total = out_cols * out_rows
    chars, colors, cell_mat = bytearray(b' ' * total), bytearray(total), bytearray(total)
    row_left, row_right = [-1] * out_rows, [-1] * out_rows
    row_half, row_mid = [0.0] * out_rows, [0.0] * out_rows
    min_r, max_r = out_rows, 0
    mbuf, mw, prio = MASK.buf, MASK.w, _MAT_PRIO
    sy_s, sx_s = (V_ROWS * SS - 2) / out_rows, (V_COLS * SS - 2) / out_cols

    # 1. Downsample 2x2 supersampled mask & compute row-by-row body profile
    for r in range(out_rows):
        my0 = int(r * sy_s)
        rm0, rm1, ro = my0 * mw, (my0 + 1) * mw, r * out_cols
        fc, lc = -1, -1
        for c in range(out_cols):
            mx0 = int(c * sx_s)
            a, b, d, e = mbuf[rm0 + mx0], mbuf[rm0 + mx0 + 1], mbuf[rm1 + mx0], mbuf[rm1 + mx0 + 1]
            m = a
            if prio[b] > prio[m]: m = b
            if prio[d] > prio[m]: m = d
            if prio[e] > prio[m]: m = e
            if m != 0:
                cell_mat[ro + c] = m
                if fc == -1: fc = c
                lc = c
        if fc != -1 and lc - fc >= 2:
            row_left[r], row_right[r] = fc, lc
            row_half[r], row_mid[r] = (lc - fc) * 0.5, (fc + lc) * 0.5
            if r < min_r: min_r = r
            if r > max_r: max_r = r

    # 2. Shade 3D ellipsoidal ASCII characters + floating kana text
    h_span = max(1, max_r - min_r)
    g_row = out_rows - 4 if out_rows >= 60 else out_rows - 2
    lathe_cut = int((state['latheCutoffRow'] / V_ROWS) * out_rows) if state['latheCutoffRow'] > 0 else 0
    vis_k, act_k, show_m = state['visibleKanaCount'], state['activeKanaIdx'], state['showMotionLines']
    b_max, t_max, sqrt, letters = len(BODY_RAMP_BYTES) - 1, len(TAIL_RAMP_BYTES) - 1, math.sqrt, 0

    for r in range(2, out_rows - 1):
        vr, ro = int((r / out_rows) * V_ROWS), r * out_cols
        rl, rr = row_left[r], row_right[r]
        hw = row_half[r] if row_half[r] > 4.0 else 4.0
        mc_row = row_mid[r]
        ny = ((r - min_r) / h_span) * 2.0 - 1.0
        ny2 = ny * ny * 0.35

        if lathe_cut > 0 and r < lathe_cut:
            if r >= lathe_cut - 4 and row_half[lathe_cut] > 4.0:
                roff = lathe_cut - r
                hsp, mid_l, col_l = row_half[lathe_cut] + roff * 1.8, row_mid[lathe_cut], (20 if roff == 1 else 21)
                for c in range(1, out_cols - 1):
                    if abs(c - mid_l) <= hsp:
                        chars[ro + c], colors[ro + c] = 46, col_l
                        letters += 1
            continue

        for c in range(1, out_cols - 1):
            vc, idx = int((c / out_cols) * V_COLS), ro + c

            if vis_k > 0 and 4 <= vc <= 20 and 7 <= vr <= 60:
                ki, kr = (vr - 7) // 9, (vr - 7) % 9
                if 0 <= ki < vis_k and ki < 6 and kr < 8:
                    newest = (ki == act_k)
                    kc = vc - 5 - (round(math.sin(t * 24.0)) if newest else 0)
                    grow = KANA_GLYPHS[ki][kr]
                    if 0 <= kc < len(grow) and grow[kc] != ' ':
                        gch = grow[kc]
                        chars[idx] = ord(gch)
                        colors[idx] = (13 if newest else 14) if gch in ('0', '8') else (15 if newest else 16)
                        letters += 1
                        continue

            m = cell_mat[idx]
            if m != 0:
                dsil = (c - rl) if (c - rl) < (rr - c) else (rr - c)
                outer = (dsil <= 1)
                nx = (c - mc_row) / hw
                rsq = nx * nx * 0.85 + ny2
                lum = -0.34 * nx - 0.16 * ny + 0.86 * (sqrt(1.0 - rsq) if rsq < 1.0 else 0.0)
                if outer: lum *= 0.28
                elif dsil == 2: lum *= 0.65
                lum = 0.0 if lum < 0.0 else (1.0 if lum > 1.0 else lum)

                if m == 5:   chars[idx], colors[idx] = 48, 12
                elif m == 3: chars[idx], colors[idx] = (48 if lum > 0.65 else 83), 9
                elif m == 4: chars[idx], colors[idx] = (83 if lum > 0.60 else 36), 10
                elif m == 6: chars[idx], colors[idx] = 47, 11
                elif m == 2:
                    chars[idx] = TAIL_RAMP_BYTES[int(lum * t_max)]
                    colors[idx] = 5 if outer else (6 if lum > 0.70 else (7 if lum > 0.45 else 8))
                else:
                    chars[idx] = BODY_RAMP_BYTES[int(lum * b_max)]
                    colors[idx] = 1 if outer else (2 if lum > 0.74 else (3 if lum > 0.46 else 4))
                letters += 1
                continue

            if show_m and (((vr == 18 or vr == 20) and 22 <= vc <= 27) or ((vr == 13 or vr == 15) and 74 <= vc <= 79)):
                if math.sin(t * 28.0 + r) > 0.0:
                    chars[idx], colors[idx] = 45, 19
                    letters += 1
                    continue

            if r == g_row or r == g_row + 1:
                dc = abs(vc - int(V_COLS * 0.55))
                if dc < 38:
                    sh = (dc < 15)
                    chars[idx], colors[idx] = (110 if (sh and r == g_row) else 46), (18 if sh else 17)
                    letters += 1

    return chars, colors, dict(
        cols=out_cols, rows=out_rows, rotDeg=state['rotDeg'], phaseName=state['phaseName'],
        sceneLabel=state['sceneLabel'], activeFn=state['activeFn'], letters=letters,
    )


def render_frame_ansi(t, term_cols, term_rows):
    """Terminal ANSI Renderer: converts `render_frame_grid()` output into TrueColor text."""
    out_cols = min(V_COLS, max(64, term_cols - 1))
    out_rows = min(V_ROWS, max(28, term_rows - 2))
    chars, colors, meta = render_frame_grid(t, out_cols, out_rows)
    ansi = PALETTE_ANSI
    lines = []
    for r in range(2, out_rows - 1):
        ro, parts, last_c = r * out_cols, [], -1
        for c in range(out_cols):
            col, ch = colors[ro + c], chr(chars[ro + c])
            if col != last_c:
                parts.append(ansi[col] if col else RESET)
                last_c = col
            parts.append(ch)
        if last_c > 0:
            parts.append(RESET)
        lines.append("".join(parts))

    hud = "%smomonga.py · ascii%s   rotation %.2f°   %s   letters %s   t %05.2fs%s" % (
        C_HUD, C_HUD_DIM, meta['rotDeg'], meta['phaseName'], "{:,}".format(meta['letters']), t % 99.99, RESET
    )
    tip = "%s[Tip: Press Ctrl + '-' to zoom out for full 124x72 HD detail]%s" % (C_HUD_DIM, RESET) if out_rows < 52 else ""
    return HOME + hud + "\n" + tip + "\n" + "\n".join(lines) + RESET


def serve_web():
    """Starts a local HTTP server in this folder and opens index.html with momonga.py auto-loaded."""
    import functools
    import webbrowser
    from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

    folder = os.path.dirname(os.path.abspath(__file__))
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(SimpleHTTPRequestHandler, directory=folder))
    url = "http://127.0.0.1:%d/index.html" % server.server_address[1]
    print("[momonga.py] Serving %s at %s (Press Ctrl+C to stop)" % (folder, url))
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("\n[momonga.py] Web server stopped.")


def main():
    if "--web" in sys.argv:
        serve_web()
        return
    os.system('cls' if os.name == 'nt' else 'clear')
    sys.stdout.write(HIDE_CURSOR)
    sys.stdout.flush()
    t0 = time.time()
    try:
        while True:
            t = time.time() - t0
            ts = shutil.get_terminal_size((124, 76))
            sys.stdout.write(render_frame_ansi(t, ts.columns, ts.lines))
            sys.stdout.flush()
            time.sleep(max(0.004, (1.0 / 30.0) - ((time.time() - t0) - t)))
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW_CURSOR + RESET + "\n\n[momonga.py] Animation stopped.\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()