"""File writers built on layout.pixel_rows(): the Eos pixel map CSV (START_PIXELMAP block),
an Eos-importable patch sheet, and an SVG preview of the grid."""
import csv
import io

from .layout import pixel_rows

PALETTE = ["#e53935", "#43a047", "#1e88e5", "#fdd835", "#d81b60", "#00acc1", "#fb8c00", "#8e24aa"]


def eos_pixelmap_csv(params):
    """Eos Pixel Map CSV. mode=address: ADDRESS + MANUFACTURER/FIXTURE_TYPE (Eos creates
    address-type pixels). mode=channel: CHANNEL = channel_base + global index (pre-patched)."""
    rows, size, straddle = pixel_rows(params)
    if straddle and params.get("allow_straddle") != "1":
        raise ValueError(f"{straddle} pixels would straddle a universe boundary; set channels per "
                         f"universe to 510 (or pass allow_straddle=1)")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["START_PIXELMAP"])
    if params.get("mode") == "channel":
        base = int(params.get("channel_base") or 1)
        w.writerow(["PIXELMAP", "LOCATION_X", "LOCATION_Y", "CHANNEL"])
        for r in rows:
            w.writerow([r["pixelmap"], r["x"], r["y"], base + r["global_index"]])
    else:
        mfr = params.get("manufacturer") or "Generic"
        w.writerow(["PIXELMAP", "LOCATION_X", "LOCATION_Y", "ADDRESS", "MANUFACTURER", "FIXTURE_TYPE"])
        for r in rows:
            w.writerow([r["pixelmap"], r["x"], r["y"], r["address"], mfr,
                        params.get("fixture_type") or r["profile"]])
    w.writerow(["END_PIXELMAP"])
    return buf.getvalue(), size, straddle


def eos_patch_csv(params):
    rows, size, straddle = pixel_rows(params)
    base = int(params.get("channel_base") or 1)
    mfr = params.get("manufacturer") or "Generic"
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["CHANNEL", "ADDRESS", "PORT_OFFSET", "MANUFACTURER", "FIXTURE_TYPE", "LABEL",
                "CONTROLLER", "IP", "LED_INDEX", "PORT", "LED", "DDP_BYTE", "PIXELMAP", "LOCATION_X", "LOCATION_Y"])
    for r in rows:
        w.writerow([base + r["global_index"], r["address"], f"{r['universe']}/{r['offset']}", mfr,
                    params.get("fixture_type") or r["profile"], f"{r['controller']} p{r['port']} #{r['led']}",
                    r["controller"], r["ip"], r["index"], r["port"], r["led"], r["ddp"],
                    r["pixelmap"], r["x"], r["y"]])
    return buf.getvalue()


def preview_svg(params):
    rows, size, straddle = pixel_rows(params)
    cell = 8
    maps = sorted({r["pixelmap"] for r in rows})
    maxx, maxy = max(r["x"] for r in rows), max(r["y"] for r in rows)
    W, H = maxx * cell + 40, (maxy * cell + 40) * len(maps)
    ctrl_ids = {}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">',
           '<rect width="100%" height="100%" fill="#111"/>']
    for mi, pm in enumerate(maps):
        oy = mi * (maxy * cell + 40)
        out.append(f'<text x="8" y="{oy + 16}" fill="#ccc" font-family="sans-serif" font-size="12">'
                   f'Pixel map {pm}: {size["W"]} x {size["H"]} cells, {size["nudged"]} nudged, '
                   f'{straddle or "no"} straddling universe boundaries</text>')
        for r in rows:
            if r["pixelmap"] != pm:
                continue
            ci = ctrl_ids.setdefault(r["controller"], len(ctrl_ids))
            out.append(f'<rect x="{20 + (r["x"] - 1) * cell}" y="{oy + 24 + (r["y"] - 1) * cell}" '
                       f'width="{cell - 1}" height="{cell - 1}" fill="{PALETTE[ci % len(PALETTE)]}">'
                       f'<title>{r["controller"]} idx {r["index"]} port {r["port"]} led {r["led"]} '
                       f'addr {r["address"]} ({r["universe"]}/{r["offset"]}) ddp {r["ddp"]}</title></rect>')
    out.append("</svg>")
    return "\n".join(out)
