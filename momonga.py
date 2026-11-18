#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
momonga.py - Single-Source-of-Truth 3D ASCII Animation Engine (Pure Python)
  - Draws Momonga (Chiikawa fan-art) using a fast Scanline Vector/Bezier Rasterizer.
  - Computes 3D ellipsoidal ASCII shading per row.
  - Exposes `render_frame_grid(...)` for ANY renderer (Terminal ANSI, Web Canvas via Pyodide, etc.).
  - Run directly in Terminal: `python momonga.py` (Zero external dependencies!).
"""
import os
import sys
import time
import math
import shutil

if os.name == 'nt':
    os.system('')

ESC = chr(27)
HIDE_CURSOR = ESC + "[?25l"
SHOW_CURSOR = ESC + "[?25h"
HOME = ESC + "[H"
RESET = ESC + "[0m"


def _ansi_rgb(r, g, b):
    return "%s[38;2;%d;%d;%dm" % (ESC, r, g, b)


# Shared RGB Color Palette (used by both Terminal ANSI and Web Canvas Renderer)
PALETTE_RGB = [
    (5, 5, 7),        # 0: BG
    (150, 142, 126),  # 1: OUTLINE
    (253, 251, 244),  # 2: WHITE_HI
    (235, 229, 213),  # 3: WHITE_MID
    (194, 186, 166),  # 4: WHITE_SH
    (88, 144, 176),   # 5: TAIL_RIM
    (196, 242, 255),  # 6: TAIL_HI
    (142, 218, 255),  # 7: TAIL_MID
    (104, 180, 220),  # 8: TAIL_SH
    (158, 232, 255),  # 9: NOSE_BLUE
    (245, 148, 170),  # 10: BLUSH
    (74, 44, 32),     # 11: SLASH
    (255, 255, 255),  # 12: EYE_HI
    (255, 247, 250),  # 13: KANA_CORE_NEW
    (232, 213, 220),  # 14: KANA_CORE_OLD
    (235, 138, 166),  # 15: KANA_RIM_NEW
    (168, 96, 118),   # 16: KANA_RIM_OLD
    (58, 56, 50),     # 17: GROUND
    (110, 106, 96),   # 18: G_SHADOW
    (140, 133, 120),  # 19: MOTION_LINE
    (214, 209, 196),  # 20: LATHE_BRIGHT
    (140, 134, 122),  # 21: LATHE_DIM
]

PALETTE_HEX = ["#%02x%02x%02x" % rgb for rgb in PALETTE_RGB]
PALETTE_ANSI = [_ansi_rgb(r, g, b) for r, g, b in PALETTE_RGB]
C_HUD = _ansi_rgb(212, 212, 216)
C_HUD_DIM = _ansi_rgb(148, 152, 164)

BODY_RAMP = "na0QOB86#$S51000"
TAIL_RAMP = "no0QB86#$S500"
BODY_RAMP_BYTES = BODY_RAMP.encode('ascii')
TAIL_RAMP_BYTES = TAIL_RAMP.encode('ascii')

KANA_GLYPHS = [
    ["  #8x.    #8. ", " 8000#    #08x", " #000#     #00", " #000#     .80", " +800#  ..  #0", "  #008.+8#  ..", "   x8000#     ", "    .+##.     "],
    ["    .#8.  #8x ", "   .#00.  .#0x", " .#80000###00#", " #008.#008.800", "  ..  #000.#00", "     .#008x80#", "      #000.   ", "      .#8x    "],
    ["    .#8.  #8x ", "   .#00.  .#0x", " .#80000###00#", " #008.#008.800", "  ..  #000.#00", "     .#008x80#", "      #000.   ", "      .#8x    "],
    ["   .#8x   #x# ", " #800000#.#x# ", "   #008.      ", "  .#000. #800x", "  .#008.   .. ", "  #000. +8.   ", "  #008. #0000#", "  .#8.   x800#"],
    ["    .#8.  #8x ", "   .#00.  .#0x", " .#80000###00#", " #008.#008.800", "  ..  #000.#00", "     .#008x80#", "      #000.   ", "      .#8x    "],
    ["   .#8x   #x# ", " #800000#.#x# ", "   #008.      ", "  .#000. #800x", "  .#008.   .. ", "  #000. +8.   ", "  #008. #0000#", "  .#8.   x800#"]
]


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
    """
    Fast Pure-Python Scanline 2D Rasterizer (248x152 supersampled mask).
    Uses analytical quadratic scanlines + C-level bytearray slice assignment
    so it runs smoothly at 30 FPS in both native Python and Pyodide WebAssembly.
    Material IDs:
      0 = Black / Cutout    1 = White Body       2 = Sky-Blue Tail
      3 = Cyan Nose/Ear     4 = Pink Blush      5 = Eye Highlight
      6 = Blush Slash
    """
    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.buf = bytearray(w * h)
        self._zero = bytes(w * h)
        self._fills = [bytes([m]) * w for m in range(7)]
        self.tx = 0.0
        self.ty = 0.0
        self.rot = 0.0
        self.sx = 1.0
        self.sy = 1.0
        self.cr = 1.0
        self.sr = 0.0
        self.stack = []

    def clear(self):
        self.buf[:] = self._zero

    def save(self):
        self.stack.append((self.tx, self.ty, self.rot, self.sx, self.sy, self.cr, self.sr))

    def restore(self):
        self.tx, self.ty, self.rot, self.sx, self.sy, self.cr, self.sr = self.stack.pop()

    def set_pose(self, cx, cy, rot, squash):
        self.tx = cx
        self.ty = cy
        self.rot = rot
        self.sx = squash
        self.sy = 1.0 / squash
        self.cr = math.cos(rot)
        self.sr = math.sin(rot)

    def _to_screen(self, x, y):
        xs = x * self.sx
        ys = y * self.sy
        return (self.tx + xs * self.cr - ys * self.sr,
                self.ty + xs * self.sr + ys * self.cr)

    def fill_ellipse(self, cx, cy, rx, ry, erot, mat):
        scx, scy = self._to_screen(cx, cy)
        total_rot = self.rot + erot
        ca = math.cos(-total_rot)
        sa = math.sin(-total_rot)
        rx_s = max(0.8, rx * self.sx)
        ry_s = max(0.8, ry * self.sy)

        inv_rx2 = 1.0 / (rx_s * rx_s)
        inv_ry2 = 1.0 / (ry_s * ry_s)
        A = ca * ca * inv_rx2 + sa * sa * inv_ry2
        inv_2A = 0.5 / A
        b_coef = 2.0 * ca * sa * (inv_ry2 - inv_rx2)
        c_coef = sa * sa * inv_rx2 + ca * ca * inv_ry2

        max_ry = math.sqrt(rx_s * rx_s * sa * sa + ry_s * ry_s * ca * ca) + 1.0
        y0 = max(0, int(scy - max_ry))
        y1 = min(self.h - 1, int(scy + max_ry))

        w = self.w
        buf = self.buf
        fill = self._fills[mat]
        sqrt = math.sqrt

        for py in range(y0, y1 + 1):
            dy = py - scy
            B = b_coef * dy
            C = c_coef * dy * dy - 1.0
            D = B * B - 4.0 * A * C
            if D >= 0.0:
                sq = sqrt(D)
                x_lo = int(math.ceil(scx + (-B - sq) * inv_2A))
                x_hi = int(math.floor(scx + (-B + sq) * inv_2A))
                if x_lo < 0:
                    x_lo = 0
                if x_hi >= w:
                    x_hi = w - 1
                if x_lo <= x_hi:
                    row_off = py * w
                    buf[row_off + x_lo : row_off + x_hi + 1] = fill[: x_hi - x_lo + 1]

    def stroke_line(self, x0, y0, x1, y1, width, mat):
        sx0, sy0 = self._to_screen(x0, y0)
        sx1, sy1 = self._to_screen(x1, y1)
        dist = math.hypot(sx1 - sx0, sy1 - sy0)
        rad = max(0.6, width * 0.5)
        steps = max(2, int(dist / max(1.0, rad * 0.65)))
        r2 = rad * rad
        ir = int(rad + 0.5)
        w, h, buf = self.w, self.h, self.buf
        fill = self._fills[mat]
        sqrt = math.sqrt

        for i in range(steps + 1):
            u = i / steps
            px = sx0 + (sx1 - sx0) * u
            py = sy0 + (sy1 - sy0) * u
            iy0 = max(0, int(py - ir))
            iy1 = min(h - 1, int(py + ir))
            for yy in range(iy0, iy1 + 1):
                dy = yy - py
                rem = r2 - dy * dy
                if rem >= 0.0:
                    dx = sqrt(rem)
                    x_lo = max(0, int(px - dx))
                    x_hi = min(w - 1, int(px + dx))
                    if x_lo <= x_hi:
                        roff = yy * w
                        buf[roff + x_lo : roff + x_hi + 1] = fill[: x_hi - x_lo + 1]

    def stroke_curve(self, x0, y0, cx1, cy1, x1, y1, width, mat):
        steps = 8
        px_prev, py_prev = x0, y0
        for i in range(1, steps + 1):
            t = i / steps
            px = bezier_pt(x0, cx1, x1, t)
            py = bezier_pt(y0, cy1, y1, t)
            self.stroke_line(px_prev, py_prev, px, py, width, mat)
            px_prev, py_prev = px, py

    def fill_tuft(self, bx0, by0, tx1, ty1, bx2, by2, mat):
        for i in range(5):
            u = i / 4.0
            mx = bx0 + (bx2 - bx0) * u
            my = by0 + (by2 - by0) * u
            self.stroke_line(mx, my, tx1, ty1, 3.6, mat)


def draw_curved_plume_tail(mc, rootX, rootY, ctrlX, ctrlY, tipX, tipY, baseRadius):
    steps = 14
    for i in range(steps + 1):
        t = i / steps
        px = bezier_pt(rootX, ctrlX, tipX, t)
        py = bezier_pt(rootY, ctrlY, tipY, t)
        rScale = 0.52 + 0.58 * math.sin(t * math.pi * 0.82)
        mc.fill_ellipse(px, py, baseRadius * rScale, baseRadius * rScale * 0.96, 0.0, 2)

    num_rows = 6
    for side in (-1, 1):
        for k in range(num_rows):
            t = 0.14 + (k / (num_rows - 1)) * 0.78
            sx = bezier_pt(rootX, ctrlX, tipX, t)
            sy = bezier_pt(rootY, ctrlY, tipY, t)
            dx = bezier_tan(rootX, ctrlX, tipX, t)
            dy = bezier_tan(rootY, ctrlY, tipY, t)
            ln = math.hypot(dx, dy) or 1.0
            tx, ty = dx / ln, dy / ln
            nx, ny = side * (-ty), side * tx
            rLocal = baseRadius * (0.52 + 0.58 * math.sin(t * math.pi * 0.82))
            bx = sx + nx * (rLocal * 0.76)
            by = sy + ny * (rLocal * 0.76)
            tipOutX = sx + nx * (rLocal + 9.5) + tx * 4.5
            tipOutY = sy + ny * (rLocal + 9.5) + ty * 4.5
            mc.fill_tuft(bx - tx * 7.5, by - ty * 7.5, tipOutX, tipOutY, bx + tx * 7.5, by + ty * 7.5, 2)

            lx0 = sx + nx * (rLocal * 0.36) - tx * 2.0
            ly0 = sy + ny * (rLocal * 0.36) - ty * 2.0
            lx1 = sx + nx * (rLocal + 6.5) + tx * 2.5
            ly1 = sy + ny * (rLocal + 6.5) + ty * 2.5
            cpx = (lx0 + lx1) * 0.5 + tx * 4.8
            cpy = (ly0 + ly1) * 0.5 + ty * 4.8
            mc.stroke_curve(lx0, ly0, cpx, cpy, lx1, ly1, 2.8, 0)


def draw_momonga_front_pose(mc, pose):
    cx = mc.w * 0.54 + pose['shiftX']
    cy = mc.h * 0.54 - pose['hopY'] * 22.0
    mc.save()
    mc.set_pose(cx, cy, pose['tilt'], pose['squash'])

    # 1. Plume Tail Behind Body
    draw_curved_plume_tail(
        mc,
        pose['tailRootX'], pose['tailRootY'],
        pose['tailCtrlX'], pose['tailCtrlY'],
        pose['tailTipX'],  pose['tailTipY'],
        25.5
    )

    # Crisp black separation border around white head & torso
    mc.fill_ellipse(0, -5, 47.0, 38.0, 0.0, 0)
    mc.fill_ellipse(0, 17, 42.0, 32.5, 0.0, 0)

    # 2. Round Ears with Light-Blue Inner Ear
    earWiggle = math.sin(pose['wagPhase']) * 0.04
    mc.fill_ellipse(-25, -42, 13.5, 17.5, -0.20 + earWiggle, 0)
    mc.fill_ellipse(-25, -42, 10.8, 14.8, -0.20 + earWiggle, 1)
    mc.fill_ellipse(-24, -41, 6.2, 9.5, -0.20 + earWiggle, 3)

    mc.fill_ellipse(25, -42, 13.5, 17.5, 0.20 - earWiggle, 0)
    mc.fill_ellipse(25, -42, 10.8, 14.8, 0.20 - earWiggle, 1)
    mc.fill_ellipse(24, -41, 6.2, 9.5, 0.20 - earWiggle, 3)

    # 3. Plump White Head + Pear-Shaped Torso
    mc.fill_ellipse(0, -5, 44.2, 35.2, 0.0, 1)
    mc.fill_ellipse(0, 17, 39.2, 29.8, 0.0, 1)

    mc.stroke_line(-20, -30, -23, -39, 2.7, 0)
    mc.stroke_line(-20, -32, -27, -35, 2.5, 0)
    mc.stroke_line(20, -30, 23, -39, 2.7, 0)
    mc.stroke_line(20, -32, 27, -35, 2.5, 0)

    # 4. 3 Spiky Cheek Tufts on Left & Right
    tuftYs = (-5, 2, 9)
    for i in range(3):
        ty = tuftYs[i]
        dy = (i - 1) * 2.6
        mc.stroke_line(-40, ty, -53, ty + dy, 6.4, 1)
        mc.stroke_line(-41, ty - 3.3, -53, ty + dy - 3.3, 2.4, 0)
        mc.stroke_line(40, ty, 53, ty + dy, 6.4, 1)
        mc.stroke_line(41, ty - 3.3, 53, ty + dy - 3.3, 2.4, 0)

    # 5. Smooth Alternating Legs
    lLift = pose['leftLift']
    if lLift < 0.55:
        footY = lerp(55.0, 46.0, lLift / 0.55)
        mc.stroke_line(-9, 41, -9, footY, 12.0, 1)
    else:
        archY = lerp(45.0, 40.0, (lLift - 0.55) / 0.45)
        mc.fill_ellipse(-12, archY, 11.0, 12.0, 0.12, 0)
        mc.fill_ellipse(-12, archY + 1.0, 8.4, 9.5, 0.12, 1)

    rLift = pose['rightLift']
    if rLift < 0.55:
        footY = lerp(55.0, 46.0, rLift / 0.55)
        mc.stroke_line(9, 41, 9, footY, 12.0, 1)
    else:
        archY = lerp(45.0, 40.0, (rLift - 0.55) / 0.45)
        mc.fill_ellipse(12, archY, 11.0, 12.0, -0.12, 0)
        mc.fill_ellipse(12, archY + 1.0, 8.4, 9.5, -0.12, 1)

    # 6. Smooth Interpolated Arms
    lCurl = pose['leftCurl']
    lx0, ly0 = lerp(-34, -24, lCurl), lerp(16, 22, lCurl)
    lcx, lcy = lerp(-41, -12, lCurl), lerp(21, 15, lCurl)
    lx1, ly1 = lerp(-47, -6, lCurl),  lerp(26, 21, lCurl)
    mc.stroke_curve(lx0, ly0, lcx, lcy, lx1, ly1, 12.2, 0)
    mc.stroke_curve(lx0, ly0, lcx, lcy, lx1, ly1, 8.2, 1)

    rState = pose['rightState']
    if rState < 0:
        u = -rState
        rx0, ry0 = lerp(34, 24, u), lerp(16, 20, u)
        rcx, rcy = lerp(41, 12, u), lerp(21, 14, u)
        rx1, ry1 = lerp(47, 6, u),  lerp(26, 20, u)
    else:
        u = rState
        rx0, ry0 = lerp(34, 35, u), lerp(16, -3, u)
        rcx, rcy = lerp(41, 45, u), lerp(21, -12, u)
        rx1, ry1 = lerp(47, 53, u), lerp(26, -21, u)
    mc.stroke_curve(rx0, ry0, rcx, rcy, rx1, ry1, 12.2, 0)
    mc.stroke_curve(rx0, ry0, rcx, rcy, rx1, ry1, 8.2, 1)

    # 7. Smug Half-Lidded Momonga Face
    mc.fill_ellipse(-17, -5, 9.8, 7.5, -0.08, 0)
    mc.fill_ellipse(17, -5, 9.8, 7.5, 0.08, 0)
    mc.fill_ellipse(-17, -6.6, 6.8, 4.0, -0.08, 5)
    mc.fill_ellipse(17, -6.6, 6.8, 4.0, 0.08, 5)

    mc.stroke_line(-10.5, -15, -7.2, -13.2, 2.5, 0)
    mc.stroke_line(10.5, -15, 7.2, -13.2, 2.5, 0)

    mc.fill_ellipse(0, -5, 5.4, 4.4, 0.0, 3)

    mc.stroke_curve(-5.8, 2.5, -2.8, 7.0, 0, 2.2, 2.7, 0)
    mc.stroke_curve(0, 2.2, 2.8, 7.0, 5.8, 2.5, 2.7, 0)
    mc.stroke_line(-2.5, 9.5, 2.5, 9.5, 2.4, 0)

    mc.fill_ellipse(-28, 3.8, 9.8, 5.8, -0.1, 4)
    mc.fill_ellipse(28, 3.8, 9.8, 5.8, 0.1, 4)
    for s in (-1, 0, 1):
        mc.stroke_line(-28 + s * 3.7 - 1, 5.8, -28 + s * 3.7 + 1, 1.8, 2.0, 6)
        mc.stroke_line(28 + s * 3.7 - 1, 5.8, 28 + s * 3.7 + 1, 1.8, 2.0, 6)

    mc.restore()


def draw_momonga_side_profile_exact(mc, pose, wagWhip):
    cx = mc.w * 0.50 - wagWhip * 4.0
    cy = mc.h * 0.54 - pose['hopY'] * 18.0 + wagWhip * 2.5
    forwardLean = -0.06 - wagWhip * 0.14
    mc.save()
    mc.set_pose(cx, cy, forwardLean, pose['squash'])

    stepPhase = math.sin(pose['wagPhase'] * 2.0)
    leg1Lift = max(0.0, stepPhase) * 3.0
    leg2Lift = max(0.0, -stepPhase) * 3.0
    mc.stroke_line(-6, 33, -11, 50 - leg1Lift, 9.5, 1)
    mc.stroke_line(6, 33, 1, 50 - leg2Lift, 9.5, 1)
    mc.stroke_line(-1, 35, -5, 51, 3.0, 0)

    rearEarX = 2.0 - wagWhip * 3.0
    rearEarY = -36.0 + wagWhip * 2.0
    mc.fill_ellipse(rearEarX, rearEarY, 8.5, 12.0, 0.48, 1)
    mc.stroke_curve(-11, -33, -3, -33, 5, -28, 3.0, 0)

    mc.fill_ellipse(-15, -6, 30.5, 27.5, -0.16, 1)
    mc.fill_ellipse(-27, 2, 18.5, 15.0, -0.08, 1)
    mc.fill_ellipse(-6, 15, 28.5, 23.5, -0.14, 1)

    mc.fill_ellipse(-19, -37, 9.2, 13.8, 0.14, 1)
    mc.stroke_curve(-11, -36, -13, -31, -10, -27, 3.0, 0)

    tailCx = lerp(24.0, 11.0, wagWhip)
    tailCy = lerp(2.0, -7.0, wagWhip)
    tailRot = lerp(0.18, -0.32, wagWhip)

    mc.fill_ellipse(tailCx, tailCy, 24.5, 30.5, tailRot, 2)
    mc.fill_ellipse(tailCx + 4, tailCy - 6, 22.0, 25.0, tailRot - 0.1, 2)
    mc.fill_ellipse(tailCx - 4, tailCy + 10, 19.0, 20.0, tailRot + 0.15, 2)

    innerStrokes = (
        (-1, -24,  5, -20, 11, -14),
        ( 2, -16,  9, -12, 15,  -6),
        ( 5,  -7, 12,  -3, 18,   3),
        ( 6,   3, 13,   7, 19,  13),
        ( 5,  12, 12,  16, 18,  22),
        ( 3,  20,  9,  24, 15,  29)
    )
    shiftInX = -wagWhip * 13.0
    shiftInY = -wagWhip * 7.0
    for st in innerStrokes:
        mc.stroke_curve(
            st[0] + shiftInX, st[1] + shiftInY,
            st[2] + shiftInX, st[3] + shiftInY,
            st[4] + shiftInX, st[5] + shiftInY,
            3.1, 0
        )

    outerAngles = (-1.65, -1.22, -0.80, -0.38, 0.04, 0.44, 0.84, 1.24, 1.62)
    for a0 in outerAngles:
        ang = a0 + tailRot * 0.65
        ca, sa = math.cos(ang), math.sin(ang)
        rimX = tailCx + ca * 22.0
        rimY = tailCy + sa * 28.0
        spikeTipX = tailCx + ca * 31.5 - sa * 3.0
        spikeTipY = tailCy + sa * 36.5 + ca * 3.0
        mc.fill_tuft(rimX - sa * 7.5, rimY + ca * 7.5, spikeTipX, spikeTipY, rimX + sa * 7.5, rimY - ca * 7.5, 2)
        inX = tailCx + ca * 14.5 - sa * 2.0
        inY = tailCy + sa * 19.5 + ca * 2.0
        outX = tailCx + ca * 29.0 + sa * 2.0
        outY = tailCy + sa * 34.0 - ca * 2.0
        mc.stroke_curve(inX, inY, (inX + outX) * 0.5 - sa * 3.8, (inY + outY) * 0.5 + ca * 3.8, outX, outY, 3.1, 0)

    mc.fill_ellipse(-44.5, -0.5, 7.8, 5.8, -0.08, 1)
    mouthBounce = max(0.0, math.sin(pose['wagPhase'] * 2.0)) * 2.2
    mc.stroke_curve(-49.5, 0.2, -44.2, 5.2 + mouthBounce, -39.2, 0.4, 3.0, 0)

    mc.fill_ellipse(-34.0, -8.5, 6.4, 9.6, -0.08, 0)
    mc.fill_ellipse(-34.8, -10.6, 4.0, 4.8, -0.08, 5)
    mc.stroke_line(-36.5, -21.0, -33.5, -19.8, 2.8, 0)

    mc.fill_ellipse(-25.0, 2.8, 9.2, 5.5, 0.0, 4)
    for k in (-1.5, -0.5, 0.5, 1.5):
        mc.stroke_line(-25.0 + k * 2.8 - 1.2, 4.8, -25.0 + k * 2.8 + 1.2, 0.8, 2.0, 6)

    mc.stroke_curve(-12.5, -8.2, -6.0, -10.2, 1.0, -11.5, 3.1, 0)
    mc.stroke_curve(-11.5, -2.8, -5.0,  -3.5, 2.0,  -3.6, 3.1, 0)
    mc.stroke_curve(-11.5,  2.8, -5.0,   3.5, 2.0,   4.8, 3.1, 0)
    mc.stroke_curve(-10.5,  8.2, -5.0,   9.8, 0.5,  12.0, 3.1, 0)

    mc.fill_ellipse(-24.5, 18.5, 8.6, 6.4, -0.20, 1)
    mc.stroke_curve(-18.5, 13.5, -28.5, 18.5, -17.5, 23.8, 3.1, 0)
    mc.restore()


def draw_momonga_back_left_wag(mc, pose, leftWhip):
    cx = mc.w * 0.55 + leftWhip * 5.0
    cy = mc.h * 0.54 - pose['hopY'] * 18.0
    bodyTilt = leftWhip * 0.20
    mc.save()
    mc.set_pose(cx, cy, bodyTilt, pose['squash'])

    stepPhase = math.sin(pose['wagPhase'] * 2.0)
    mc.stroke_line(-9, 34, -10 + leftWhip * 3, 50 - max(0.0, -stepPhase) * 3.5, 10.0, 1)
    mc.stroke_line(8, 34, 7 + leftWhip * 2, 50 - max(0.0, stepPhase) * 3.5, 10.0, 1)
    mc.stroke_line(-1, 36, -1, 51, 3.0, 0)

    mc.fill_ellipse(-16, -39, 9.2, 13.5, -0.12, 1)
    mc.fill_ellipse(19, -38, 9.2, 13.5, 0.20, 1)

    mc.fill_ellipse(2, -6, 36.5, 30.0, 0.0, 1)
    mc.fill_ellipse(2, 15, 34.0, 25.5, 0.0, 1)

    wYs = (-9, -3, 3, 9)
    for i in range(4):
        wy = wYs[i]
        dy = (i - 1.5) * 2.2
        mc.stroke_line(34, wy, 48, wy + dy, 6.2, 1)
        mc.stroke_line(35, wy - 2.6, 48, wy + dy - 2.6, 2.8, 0)

    mc.fill_ellipse(34, 19, 8.5, 6.4, 0.20, 1)
    mc.stroke_curve(28, 14, 38, 19, 28, 24, 3.1, 0)

    # Giant fluffy sky-blue tail on the LEFT side
    tailCx = lerp(-24.0, -12.0, leftWhip)
    tailCy = lerp(4.0, -5.0, leftWhip)
    tailRot = lerp(-0.20, 0.25, leftWhip)

    mc.fill_ellipse(tailCx, tailCy, 24.5, 30.5, tailRot, 2)
    mc.fill_ellipse(tailCx - 4, tailCy - 5, 22.0, 25.0, tailRot + 0.1, 2)

    innerStrokes = (
        ( 1, -24, -5, -20, -11, -14),
        (-2, -16, -9, -12, -15,  -6),
        (-5,  -7,-12,  -3, -18,   3),
        (-6,   3,-13,   7, -19,  13),
        (-5,  12,-12,  16, -18,  22),
        (-3,  20, -9,  24, -15,  29)
    )
    shiftX = leftWhip * 12.0
    shiftY = -leftWhip * 6.0
    for st in innerStrokes:
        mc.stroke_curve(
            st[0] + shiftX, st[1] + shiftY,
            st[2] + shiftX, st[3] + shiftY,
            st[4] + shiftX, st[5] + shiftY,
            3.1, 0
        )

    outerAngles = (1.55, 1.98, 2.40, 2.82, 3.24, 3.64, 4.04, 4.44, 4.82)
    for a0 in outerAngles:
        ang = a0 + tailRot * 0.65
        ca, sa = math.cos(ang), math.sin(ang)
        rimX = tailCx + ca * 22.0
        rimY = tailCy + sa * 28.0
        spikeTipX = tailCx + ca * 31.5 + sa * 3.0
        spikeTipY = tailCy + sa * 36.5 - ca * 3.0
        mc.fill_tuft(rimX - sa * 7.5, rimY + ca * 7.5, spikeTipX, spikeTipY, rimX + sa * 7.5, rimY - ca * 7.5, 2)

        inX = tailCx + ca * 14.5 + sa * 2.0
        inY = tailCy + sa * 19.5 - ca * 2.0
        outX = tailCx + ca * 29.0 - sa * 2.0
        outY = tailCy + sa * 34.0 + ca * 2.0
        mc.stroke_curve(inX, inY, (inX + outX) * 0.5 + sa * 3.8, (inY + outY) * 0.5 - ca * 3.8, outX, outY, 3.1, 0)

    mc.restore()


def evaluate_timeline(t, mode='auto', bounce=1.0):
    """
    Timeline & Choreography Controller:
      - 'auto':          0.00s-2.15s Front Dance (No Text) -> 2.15s-13.35s 2-Sided Tail Wag
      - 'scene1_front':  Loop Front Alternating-Leg Dance only
      - 'scene2_iyayada':Loop 2-Sided Tail Wag only
      - 'lathe_profile': 3D Scanline Body-Profile Build-up
    """
    if mode == 'lathe_profile':
        buildProg = 0.28 + 0.72 * ((math.sin(t * 1.4 - math.pi / 2.0) + 1.0) * 0.5)
        return {
            'view': 'front',
            'phaseName': 'puri 0',
            'sceneLabel': 'Body Profile Lathe Mode (3D Scanline Build-up)',
            'activeFn': 'def render_frame_grid()',
            'rotDeg': 336.50 + buildProg * 20.95,
            'latheCutoffRow': int(V_ROWS * (1.0 - buildProg * 0.85)),
            'visibleKanaCount': 0,
            'activeKanaIdx': -1,
            'shiftX': 0.0,
            'hopY': 0.0,
            'squash': 1.0,
            'tilt': 0.0,
            'leftLift': 0.0,
            'rightLift': 0.0,
            'leftCurl': 1.0,
            'rightState': 0.0,
            'tailRootX': -10.0, 'tailRootY': 20.0,
            'tailCtrlX': -36.0, 'tailCtrlY': 6.0,
            'tailTipX': -28.0,  'tailTipY': -26.0,
            'tailSwing': -0.8,
            'wagPhase': t * 4.0,
            'showMotionLines': False
        }

    loopT = t % 13.35
    in_scene2 = (mode == 'scene2_iyayada') or (mode == 'auto' and loopT >= 2.15)
    scene2Time = (t % 11.20) if mode == 'scene2_iyayada' else (loopT - 2.15)

    if not in_scene2:
        cyclePos = (t * 2.8) % 4.0
        beatIdx = int(cyclePos)
        sub = cyclePos - beatIdx
        ease = 0.5 - 0.5 * math.cos(sub * math.pi)
        hopArc = math.sin(sub * math.pi)

        KF = (
            {'tilt': 0.11,  'shiftX': 4.5,  'lLift': 1.0, 'rLift': 0.0, 'lCurl': 1.0, 'rState': 0.0,  'tRx': -10, 'tRy': 20, 'tCx': -38, 'tCy': 6,   'tTx': -30, 'tTy': -26},
            {'tilt': -0.11, 'shiftX': -4.5, 'lLift': 0.0, 'rLift': 1.0, 'lCurl': 0.0, 'rState': -1.0, 'tRx': 10,  'tRy': 20, 'tCx': 38,  'tCy': 6,   'tTx': 30,  'tTy': -26},
            {'tilt': -0.14, 'shiftX': -2.5, 'lLift': 0.0, 'rLift': 1.0, 'lCurl': 1.0, 'rState': 1.0,  'tRx': 4,   'tRy': 12, 'tCx': 14,  'tCy': -22, 'tTx': -2,  'tTy': -54},
            {'tilt': 0.08,  'shiftX': 2.5,  'lLift': 1.0, 'rLift': 0.0, 'lCurl': 0.2, 'rState': 0.0,  'tRx': -8,  'tRy': 18, 'tCx': -32, 'tCy': -4,  'tTx': -24, 'tTy': -36}
        )
        k0 = KF[beatIdx]
        k1 = KF[(beatIdx + 1) % 4]
        tilt = (lerp(k0['tilt'], k1['tilt'], ease) + 0.04 * hopArc * (1 if k0['tilt'] >= 0 else -1)) * bounce
        return {
            'view': 'front',
            'phaseName': 'yah-ha %d' % beatIdx,
            'sceneLabel': 'Scene 1 (0.0s-2.15s): Front Alternating-Leg Dance (No Text)',
            'activeFn': 'def draw_momonga_front_pose()',
            'rotDeg': (360.0 + tilt * 65.0) % 360.0,
            'latheCutoffRow': 0,
            'visibleKanaCount': 0,
            'activeKanaIdx': -1,
            'shiftX': lerp(k0['shiftX'], k1['shiftX'], ease),
            'hopY': (hopArc * 0.42 - 0.06) * bounce,
            'squash': 1.0 + (0.45 - hopArc) * 0.08 * bounce,
            'tilt': tilt,
            'leftLift': lerp(k0['lLift'], k1['lLift'], ease) * (0.35 + 0.65 * hopArc),
            'rightLift': lerp(k0['rLift'], k1['rLift'], ease) * (0.35 + 0.65 * hopArc),
            'leftCurl': lerp(k0['lCurl'], k1['lCurl'], ease),
            'rightState': lerp(k0['rState'], k1['rState'], ease),
            'tailRootX': lerp(k0['tRx'], k1['tRx'], ease),
            'tailRootY': lerp(k0['tRy'], k1['tRy'], ease),
            'tailCtrlX': lerp(k0['tCx'], k1['tCx'], ease),
            'tailCtrlY': lerp(k0['tCy'], k1['tCy'], ease),
            'tailTipX':  lerp(k0['tTx'], k1['tTx'], ease),
            'tailTipY':  lerp(k0['tTy'], k1['tTy'], ease),
            'tailSwing': 0.0,
            'wagPhase': t * 7.2,
            'showMotionLines': False
        }
    else:
        kanaThresholds = (0.02, 0.35, 0.68, 0.98, 1.28, 1.58)
        visibleKanaCount = sum(scene2Time >= th for th in kanaThresholds)
        activeKanaIdx = min(5, max(0, visibleKanaCount - 1))

        wagFreq = 5.4
        rawSin = math.sin(scene2Time * wagFreq - 0.8)
        tailSwing = math.copysign(abs(rawSin) ** 0.78, rawSin)
        stepBounce = abs(math.cos(scene2Time * wagFreq - 0.8))

        return {
            'view': 'unified_iyayada',
            'phaseName': 'iyayada L' if tailSwing < 0 else 'iyayada R',
            'sceneLabel': 'Scene 2: 2-Sided Tail Wag 「いややだやだ」 · Text %d/6' % visibleKanaCount,
            'activeFn': 'def draw_momonga_side_profile_exact()' if tailSwing >= -0.18 else 'def draw_momonga_back_left_wag()',
            'rotDeg': 180.0 + (tailSwing + 1.0) * 45.0,
            'latheCutoffRow': 0,
            'visibleKanaCount': visibleKanaCount,
            'activeKanaIdx': activeKanaIdx,
            'shiftX': 0.0,
            'hopY': stepBounce * 0.26 * bounce,
            'squash': 1.0 + (0.5 - stepBounce) * 0.06 * bounce,
            'tilt': 0.0,
            'tailSwing': tailSwing,
            'wagPhase': scene2Time * wagFreq,
            'showMotionLines': True
        }


# Virtual 2x supersampled mask (124x76 cells -> 248x152 mask)
V_COLS = 124
V_ROWS = 76
SS = 2
MASK = VectorMaskCanvas(V_COLS * SS, V_ROWS * SS)


def render_frame_grid(t, out_cols=V_COLS, out_rows=V_ROWS, mode='auto', bounce=1.0):
    """
    Core Renderer-Agnostic Pipeline (used by BOTH Terminal ANSI & Web Canvas):
      1. Draws Momonga into `MASK` using pure Python vector geometry.
      2. Computes row-by-row horizontal body profile `(row_left, row_right, row_half, row_mid)`.
      3. Applies 3D ellipsoidal ASCII shading + floating kana text.
      4. Returns `(chars_bytes, colors_bytes, meta_dict)` of length `out_cols * out_rows`.
    """
    state = evaluate_timeline(t, mode, bounce)
    MASK.clear()

    if state['view'] == 'front':
        draw_momonga_front_pose(MASK, state)
    else:
        s = state['tailSwing']
        if s >= -0.18:
            wagWhip = 1.0 - smoothstep(-0.12, 0.65, s)
            draw_momonga_side_profile_exact(MASK, state, wagWhip)
        else:
            leftWhip = smoothstep(-0.95, -0.22, s)
            draw_momonga_back_left_wag(MASK, state, leftWhip)

    total = out_cols * out_rows
    chars = bytearray(b' ' * total)
    colors = bytearray(total)
    cell_mat = bytearray(total)
    row_left = [-1] * out_rows
    row_right = [-1] * out_rows
    row_half = [0.0] * out_rows
    row_mid = [0.0] * out_rows

    min_r = out_rows
    max_r = 0
    mbuf = MASK.buf
    mw = MASK.w
    sy_scale = (V_ROWS * SS - 2) / out_rows
    sx_scale = (V_COLS * SS - 2) / out_cols

    # 1. Sample 2x2 supersampled mask & build row-by-row body profile
    for r in range(out_rows):
        my0 = int(r * sy_scale)
        row_m0 = my0 * mw
        row_m1 = (my0 + 1) * mw
        row_out = r * out_cols
        first_c = -1
        last_c = -1
        for c in range(out_cols):
            mx0 = int(c * sx_scale)
            m00 = mbuf[row_m0 + mx0]
            m01 = mbuf[row_m0 + mx0 + 1]
            m10 = mbuf[row_m1 + mx0]
            m11 = mbuf[row_m1 + mx0 + 1]
            if m00 == 6 or m01 == 6 or m10 == 6 or m11 == 6:
                m = 6
            elif m00 == 5 or m01 == 5 or m10 == 5 or m11 == 5:
                m = 5
            elif m00 == 0 or m01 == 0 or m10 == 0 or m11 == 0:
                m = 0
            elif m00 == 4 or m01 == 4 or m10 == 4 or m11 == 4:
                m = 4
            elif m00 == 3 or m01 == 3 or m10 == 3 or m11 == 3:
                m = 3
            elif m00 == 2 or m01 == 2 or m10 == 2 or m11 == 2:
                m = 2
            elif m00 == 1 or m01 == 1 or m10 == 1 or m11 == 1:
                m = 1
            else:
                m = 0

            if m != 0:
                cell_mat[row_out + c] = m
                if first_c == -1:
                    first_c = c
                last_c = c

        if first_c != -1 and (last_c - first_c) >= 2:
            row_left[r] = first_c
            row_right[r] = last_c
            row_half[r] = (last_c - first_c) * 0.5
            row_mid[r] = (first_c + last_c) * 0.5
            if r < min_r:
                min_r = r
            if r > max_r:
                max_r = r

    # 2. Shade into ASCII characters + palette indices
    char_h_span = max(1, max_r - min_r)
    ground_row = out_rows - 4 if out_rows >= 60 else out_rows - 2
    lathe_cut = int((state['latheCutoffRow'] / V_ROWS) * out_rows) if state['latheCutoffRow'] > 0 else 0
    vis_kana = state['visibleKanaCount']
    act_kana = state['activeKanaIdx']
    show_motion = state['showMotionLines']
    letters = 0
    sqrt = math.sqrt
    b_max = len(BODY_RAMP_BYTES) - 1
    t_max = len(TAIL_RAMP_BYTES) - 1

    for r in range(2, out_rows - 1):
        vr = int((r / out_rows) * V_ROWS)
        row_out = r * out_cols
        rl = row_left[r]
        rr = row_right[r]
        half_w = row_half[r] if row_half[r] > 4.0 else 4.0
        mid_c = row_mid[r]
        ny = ((r - min_r) / char_h_span) * 2.0 - 1.0
        ny2_term = ny * ny * 0.35

        # Lathe scanline cutoff mode
        if lathe_cut > 0 and r < lathe_cut:
            if r >= lathe_cut - 4 and row_half[lathe_cut] > 4.0:
                ring_off = lathe_cut - r
                h_span = row_half[lathe_cut] + ring_off * 1.8
                m_c = row_mid[lathe_cut]
                c_col = 20 if ring_off == 1 else 21
                for c in range(1, out_cols - 1):
                    if abs(c - m_c) <= h_span:
                        chars[row_out + c] = 46  # '.'
                        colors[row_out + c] = c_col
                        letters += 1
            continue

        for c in range(1, out_cols - 1):
            vc = int((c / out_cols) * V_COLS)
            idx_cell = row_out + c

            # A. Progressive Floating Text 「いややだやだ」 on Left (Scene 2 ONLY)
            if vis_kana > 0 and 4 <= vc <= 20 and 7 <= vr <= 60:
                kIdx = (vr - 7) // 9
                kRow = (vr - 7) % 9
                if 0 <= kIdx < vis_kana and kIdx < 6 and kRow < 8:
                    isNewest = (kIdx == act_kana)
                    shakeX = round(math.sin(t * 24.0)) if isNewest else 0
                    kCol = vc - 5 - shakeX
                    glyph_row = KANA_GLYPHS[kIdx][kRow]
                    if 0 <= kCol < len(glyph_row):
                        gCh = glyph_row[kCol]
                        if gCh != ' ':
                            chars[idx_cell] = ord(gCh)
                            if gCh == '0' or gCh == '8':
                                colors[idx_cell] = 13 if isNewest else 14
                            else:
                                colors[idx_cell] = 15 if isNewest else 16
                            letters += 1
                            continue

            # B. 3D Revolved Character Shading
            m = cell_mat[idx_cell]
            if m != 0:
                d_left = c - rl
                d_right = rr - c
                dist_sil = d_left if d_left < d_right else d_right
                is_outer = (dist_sil <= 1)

                nx = (c - mid_c) / half_w
                rad_sq = nx * nx * 0.85 + ny2_term
                nz = sqrt(1.0 - rad_sq) if rad_sq < 1.0 else 0.0
                lum = -0.34 * nx - 0.16 * ny + 0.86 * nz
                if is_outer:
                    lum *= 0.28
                elif dist_sil == 2:
                    lum *= 0.65
                if lum < 0.0:
                    lum = 0.0
                elif lum > 1.0:
                    lum = 1.0

                if m == 5:
                    chars[idx_cell] = 48  # '0'
                    colors[idx_cell] = 12
                elif m == 3:
                    chars[idx_cell] = 48 if lum > 0.65 else 83  # '0' or 'S'
                    colors[idx_cell] = 9
                elif m == 4:
                    chars[idx_cell] = 83 if lum > 0.60 else 36  # 'S' or '$'
                    colors[idx_cell] = 10
                elif m == 6:
                    chars[idx_cell] = 47  # '/'
                    colors[idx_cell] = 11
                elif m == 2:
                    chars[idx_cell] = TAIL_RAMP_BYTES[int(lum * t_max)]
                    colors[idx_cell] = 5 if is_outer else (6 if lum > 0.70 else (7 if lum > 0.45 else 8))
                else:
                    chars[idx_cell] = BODY_RAMP_BYTES[int(lum * b_max)]
                    colors[idx_cell] = 1 if is_outer else (2 if lum > 0.74 else (3 if lum > 0.46 else 4))
                letters += 1
                continue

            # C. Motion Shake Lines in Scene 2
            if show_motion:
                if ((vr == 18 or vr == 20) and 22 <= vc <= 27) or ((vr == 13 or vr == 15) and 74 <= vc <= 79):
                    if math.sin(t * 28.0 + r) > 0.0:
                        chars[idx_cell] = 45  # '-'
                        colors[idx_cell] = 19
                        letters += 1
                        continue

            # D. Dotted Ground Plane & Foot Shadow
            if r == ground_row or r == ground_row + 1:
                dist_c = abs(vc - int(V_COLS * 0.55))
                if dist_c < 38:
                    in_sh = (dist_c < 15)
                    chars[idx_cell] = 110 if (in_sh and r == ground_row) else 46  # 'n' or '.'
                    colors[idx_cell] = 18 if in_sh else 17
                    letters += 1

    meta = {
        'cols': out_cols,
        'rows': out_rows,
        'rotDeg': state['rotDeg'],
        'phaseName': state['phaseName'],
        'sceneLabel': state['sceneLabel'],
        'activeFn': state['activeFn'],
        'letters': letters,
    }
    return chars, colors, meta


def render_frame_ansi(t, term_cols, term_rows):
    """Terminal ANSI Renderer: formats the grid returned by `render_frame_grid()`."""
    out_cols = min(V_COLS, max(64, term_cols - 1))
    out_rows = min(V_ROWS, max(28, term_rows - 2))
def serve_web():
    """Starts a local HTTP server in this folder and opens index.html so it auto-loads momonga.py."""
    import functools
    import webbrowser
    from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

    folder = os.path.dirname(os.path.abspath(__file__))
    handler = functools.partial(SimpleHTTPRequestHandler, directory=folder)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    port = server.server_address[1]
    url = "http://127.0.0.1:%d/index.html" % port
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
            frame = render_frame_ansi(t, ts.columns, ts.lines)
            sys.stdout.write(frame)
            sys.stdout.flush()

            elapsed = (time.time() - t0) - t
            time.sleep(max(0.004, (1.0 / 30.0) - elapsed))
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW_CURSOR + RESET + "\n\n[momonga.py] Animation stopped.\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()