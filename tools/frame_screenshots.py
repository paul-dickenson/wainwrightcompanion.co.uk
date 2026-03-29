#!/usr/bin/env python3
"""
frame_screenshots.py  —  Screenshot the Simulator (with device chrome) or composite into a frame PNG

── Recommended: capture the Simulator window as-is ──────────────────────────
  Navigate the Simulator to a screen, then:

    python3 tools/frame_screenshots.py --capture checklist --window --out-dir images/ --webp

  Uses macOS screencapture to grab the Simulator window including its rendered
  device chrome (Dynamic Island, buttons, bezel). No frame PNG needed.

── Alternative: composite into a downloaded frame PNG ───────────────────────
  Download a device frame PNG with a TRANSPARENT screen cutout from mockuphone.com
  and save as  tools/frame-dark.png  /  tools/frame-light.png, then:

    python3 tools/frame_screenshots.py --capture checklist --out-dir images/ --webp

── Other modes ───────────────────────────────────────────────────────────────
  Single file:
    python3 tools/frame_screenshots.py raw.png --style dark --window --out images/screen-foo.png --webp

  Batch:
    python3 tools/frame_screenshots.py --batch raw/ --out-dir images/ --style dark --webp

Output naming:
    dark   →  images/screen-{name}.png
    light  →  images/screen-{name}-light.png

Requirements:
    pip3 install Pillow
    Xcode command-line tools  (xcrun, used in --capture mode)
"""

import sys
import argparse
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow not found. Install with:  pip3 install Pillow")

SCRIPT_DIR = Path(__file__).parent


# ── Frame helpers ─────────────────────────────────────────────────────────────

def find_screen_bbox(frame: Image.Image):
    """
    Return (left, top, right, bottom) of the transparent screen cutout in the frame.
    Raises ValueError if no transparent region is found.
    """
    if frame.mode != 'RGBA':
        frame = frame.convert('RGBA')
    _, _, _, alpha = frame.split()
    # Transparent pixels (alpha < 30) mark the screen area
    mask = alpha.point(lambda v: 255 if v < 30 else 0)
    bbox = mask.getbbox()
    if not bbox:
        raise ValueError(
            "No transparent screen area found in frame PNG.\n"
            "Make sure you downloaded a frame with a transparent screen cutout."
        )
    return bbox


def composite(screenshot_path: Path, frame_path: Path, output_path: Path):
    """Resize screenshot to fit the frame's transparent cutout, composite, and save."""
    frame = Image.open(frame_path).convert('RGBA')
    screen = Image.open(screenshot_path).convert('RGBA')

    left, top, right, bottom = find_screen_bbox(frame)
    screen_w = right - left
    screen_h = bottom - top

    # Resize screenshot to fill the cutout exactly
    resized = screen.resize((screen_w, screen_h), Image.LANCZOS)

    # Build canvas: screenshot first, then frame on top
    canvas = Image.new('RGBA', frame.size, (0, 0, 0, 0))
    canvas.paste(resized, (left, top))
    canvas.paste(frame, (0, 0), mask=frame)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(output_path), 'PNG', optimize=True)
    print(f"  saved  {output_path}")
    return output_path


def resolve_frame(style: str, override: Optional[str]) -> Path:
    """Return the frame PNG path for this style, or raise a helpful error."""
    if override:
        p = Path(override)
        if not p.exists():
            sys.exit(f"Frame not found: {p}")
        return p

    # Look for tools/frame-{style}.png, then tools/frame.png
    for candidate in [
        SCRIPT_DIR / f"frame-{style}.png",
        SCRIPT_DIR / "frame.png",
    ]:
        if candidate.exists():
            return candidate

    sys.exit(
        f"No frame PNG found for style '{style}'.\n"
        f"Download a device frame with a transparent screen cutout from mockuphone.com\n"
        f"and save it as:  {SCRIPT_DIR / f'frame-{style}.png'}"
    )


# ── WebP variants ─────────────────────────────────────────────────────────────

def to_webp(png_path: Path):
    img = Image.open(png_path).convert('RGBA')
    W, H = img.size

    webp = png_path.with_suffix('.webp')
    img.save(str(webp), 'WEBP', quality=88, method=6)
    print(f"  saved  {webp}")

    w600_path = png_path.with_name(f"{png_path.stem}-600w.webp")
    h600 = round(H * 600 / W)
    img.resize((600, h600), Image.LANCZOS).save(str(w600_path), 'WEBP', quality=85, method=6)
    print(f"  saved  {w600_path}")


# ── Simulator helpers ─────────────────────────────────────────────────────────

def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"Command failed: {' '.join(cmd)}\n{r.stderr.strip()}")
    return r.stdout.strip()


def sim_get_appearance():
    return _run(['xcrun', 'simctl', 'ui', 'booted', 'appearance'])


def sim_set_appearance(mode):
    print(f"  appearance → {mode}")
    _run(['xcrun', 'simctl', 'ui', 'booted', 'appearance', mode])


def sim_screenshot(path: Path):
    _run(['xcrun', 'simctl', 'io', 'booted', 'screenshot', str(path)])


_SIM_STATUS_BAR_OVERRIDES = [
    '--time', '9:41',
    '--dataNetwork', 'wifi',
    '--wifiMode', 'active',
    '--wifiBars', '3',
    '--cellularMode', 'active',
    '--cellularBars', '4',
    '--batteryState', 'charged',
    '--batteryLevel', '100',
]


def sim_status_bar_set():
    """Override status bar to canonical screenshot values (9:41, WiFi, 100%)."""
    _run(['xcrun', 'simctl', 'status_bar', 'booted', 'override'] + _SIM_STATUS_BAR_OVERRIDES)
    print("  status bar → 9:41 · WiFi · 100%")


def sim_status_bar_clear():
    """Restore the live status bar."""
    _run(['xcrun', 'simctl', 'status_bar', 'booted', 'clear'])
    print("  status bar restored")


def _sim_window_id() -> int:
    """Return the CGWindowID of the Simulator window via a Swift one-liner."""
    swift = (
        'import Quartz\n'
        'let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as! [[String: Any]]\n'
        'for w in list {\n'
        '    if let owner = w["kCGWindowOwnerName"] as? String, owner == "Simulator",\n'
        '       let wid = w["kCGWindowNumber"] as? Int32 { print(wid); break }\n'
        '}'
    )
    r = subprocess.run(['swift', '-e', swift], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        sys.exit("Could not find Simulator window. Is the Simulator running?")
    return int(r.stdout.strip())


def _crop_to_device(img: Image.Image) -> Image.Image:
    """
    Remove the Simulator toolbar and crop to the phone device.

    Pass 1 — toolbar end.
      Scan the centre column from row 50 downward. The toolbar has a uniform
      colour; require 3 consecutive rows that differ by > 20 to mark the end.
      Centre-column detection is immune to the window corner-rounding artefact
      that fools left-edge comparisons.

    Pass 2 — phone bounding box.
      Sample the Simulator background from the far-left column at mid-image
      (always outside the phone). Build a mask of pixels that differ from that
      background by > 10, then take column/row sums to find L/R/bottom bounds.
      TOP is set to toolbar_end directly — no row-count check — so the Dynamic
      Island and outer bezel rows (which can be all-black) are included.
    """
    gray = img.convert('L')
    w, h = gray.size
    data = list(gray.getdata())

    def p(x, y):
        return data[y * w + x]

    # ── Pass 1: centre-column toolbar detection ───────────────────────────────
    toolbar_color = p(w // 2, 50)
    toolbar_end = h // 4   # fallback if no transition found
    consec = 0
    first_diff = None
    for y in range(50, h // 3):
        if abs(int(p(w // 2, y)) - int(toolbar_color)) > 20:
            if first_diff is None:
                first_diff = y
            consec += 1
            if consec >= 3:
                toolbar_end = first_diff
                break
        else:
            consec = 0
            first_diff = None

    # ── Pass 2: phone bounding box ────────────────────────────────────────────
    bg = p(5, h * 2 // 3)   # background colour: left edge (outside phone), mid-image

    sub_data = data[toolbar_end * w:]
    sub_h = h - toolbar_end
    mask = [1 if abs(int(v) - int(bg)) > 10 else 0 for v in sub_data]

    # Column sums and row sums over the sub-image
    col_sums = [sum(mask[x::w]) for x in range(w)]
    row_sums = [sum(mask[y * w:(y + 1) * w]) for y in range(sub_h)]

    left_x = next((x for x, c in enumerate(col_sums) if c > 0), 0)
    right_x = next((x for x in range(w - 1, left_x, -1) if col_sums[x] > 0), w - 1)
    bottom_y = next((y for y in range(sub_h - 1, -1, -1) if row_sums[y] > 0), sub_h - 1)

    pad_tb = 8    # top / bottom
    pad_lr = 28   # left / right — buttons protrude beyond the bezel
    return img.crop((
        max(0, left_x - pad_lr),
        max(0, toolbar_end),              # top = toolbar end; no padding to avoid toolbar rows
        min(w, right_x + pad_lr),
        min(h, toolbar_end + bottom_y + pad_tb),
    ))


def capture_sim_window(output_path: Path):
    """
    Capture the Simulator window as displayed on screen — device chrome included.
    Requires Screen Recording permission for Terminal (or whatever runs this script):
      System Settings → Privacy & Security → Screen Recording → enable Terminal
    """
    # Bring Simulator to front and restore from dock/minimised state
    subprocess.run(
        ['osascript', '-e', 'tell application "Simulator" to activate'],
        capture_output=True
    )
    time.sleep(0.4)  # wait for window to fully restore

    wid = _sim_window_id()
    # -l = specific window, -o = no drop shadow, -x = no shutter sound
    r = subprocess.run(['screencapture', '-l', str(wid), '-o', '-x', str(output_path)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not output_path.exists():
        sys.exit(
            "screencapture failed — Screen Recording permission is required.\n\n"
            "  System Settings → Privacy & Security → Screen Recording\n"
            "  → enable Terminal (or the app running this script)\n\n"
            "Then re-run this command."
        )

    # Crop out the Simulator toolbar — keep only the phone device itself
    img = Image.open(str(output_path))
    cropped = _crop_to_device(img)
    cropped.save(str(output_path), 'PNG', optimize=True)
    print(f"  captured simulator window  (window id {wid})")


# ── Capture mode ──────────────────────────────────────────────────────────────

def capture_and_frame(name: str, out_dir: Path, webp: bool, delay: float,
                      frame_override: Optional[str], use_window: bool):
    # Bring Simulator to front before doing anything — avoids screencapture failure
    # if the window is minimised or on a different Space.
    if use_window:
        subprocess.run(['osascript', '-e', 'tell application "Simulator" to activate'],
                       capture_output=True)
        time.sleep(0.8)

    original = sim_get_appearance()
    print(f"Current appearance: {original}  (will restore after capture)\n")
    out_dir.mkdir(parents=True, exist_ok=True)

    outputs = {
        'dark':  out_dir / f"screen-{name}.png",
        'light': out_dir / f"screen-{name}-light.png",
    }

    sim_status_bar_set()

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for style in ('dark', 'light'):
                print(f"── {style} ──────────────")
                sim_set_appearance(style)
                print(f"  waiting {delay}s for transition…")
                time.sleep(delay)

                raw = tmp / f"{name}-{style}.png"

                if use_window:
                    capture_sim_window(raw)
                    import shutil
                    shutil.copy(raw, outputs[style])
                    print(f"  saved  {outputs[style]}")
                else:
                    sim_screenshot(raw)
                    print(f"  captured raw screenshot")
                    frame_path = resolve_frame(style, frame_override)
                    composite(raw, frame_path, outputs[style])

                if webp:
                    to_webp(outputs[style])
    finally:
        print(f"\n── restoring ──")
        sim_set_appearance(original)
        sim_status_bar_clear()

    print("\nDone.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description='Composite Simulator screenshots into a device frame PNG',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument('--capture', metavar='NAME',
                   help='Screen name (e.g. checklist). Simulator must be open on that screen.')
    p.add_argument('--window', action='store_true',
                   help='Capture the Simulator window as-is (device chrome included). '
                        'Recommended — no frame PNG needed.')
    p.add_argument('--frame', metavar='PATH',
                   help='Override frame PNG path (default: tools/frame-{style}.png)')
    p.add_argument('--delay', type=float, default=0.8, metavar='SECS',
                   help='Seconds to wait after toggling appearance (default: 0.8)')

    p.add_argument('input', nargs='?', help='Input PNG screenshot')
    p.add_argument('--style', choices=['dark', 'light'], default='dark')
    p.add_argument('--out', metavar='PATH', help='Output path')

    p.add_argument('--batch', metavar='DIR', help='Process all PNGs in DIR')
    p.add_argument('--out-dir', metavar='DIR', help='Output directory')
    p.add_argument('--webp', action='store_true',
                   help='Also generate .webp and -600w.webp variants')

    args = p.parse_args()

    if args.capture:
        out_dir = Path(args.out_dir) if args.out_dir else Path('images')
        capture_and_frame(args.capture, out_dir, args.webp, args.delay, args.frame, args.window)

    elif args.batch:
        src = Path(args.batch)
        out_dir = Path(args.out_dir) if args.out_dir else src
        frame_path = resolve_frame(args.style, args.frame)
        pngs = sorted(src.glob('*.png'))
        if not pngs:
            sys.exit(f"No PNG files found in {src}")
        for png in pngs:
            name = png.stem.removeprefix('raw-')
            suffix = '' if args.style == 'dark' else f'-{args.style}'
            out = out_dir / f"screen-{name}{suffix}.png"
            print(f"\n{png.name}  →  {out.name}")
            framed = composite(png, frame_path, out)
            if args.webp:
                to_webp(framed)

    elif args.input:
        inp = Path(args.input)
        frame_path = resolve_frame(args.style, args.frame)
        out = Path(args.out) if args.out else inp.parent / f"{inp.stem}-framed-{args.style}.png"
        print(f"\n{inp.name}  →  {out.name}")
        framed = composite(inp, frame_path, out)
        if args.webp:
            to_webp(framed)

    else:
        p.print_help()


if __name__ == '__main__':
    main()
