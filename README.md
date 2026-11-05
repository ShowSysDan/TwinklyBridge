# twinkly-bridge

Drive **Twinkly Pro** controllers per-pixel from an **ETC Eos** console (or anything else
that speaks sACN or DDP). The bridge logs into each controller over its local HTTP API, puts
it in real-time mode, and streams Eos's sACN output to it over UDP. Its web UI handles
patching, shows the controllers' camera maps lit with the frames going out, and exports the
map as an **Eos Pixel Map CSV** built from the bridge's own patch, so the desk and the bridge
can't disagree about an address.

```
Eos (sACN) --> twinkly-bridge (web UI :5000) --HTTP + UDP 7777--> controller 1  (VLAN B)
                                                              --> controller 2
                                                              --> ...
```

Twinkly Pro's own Art-Net support is a 10-channel "whole controller" fixture (brightness,
mode, R G B W, movie, playlist, on/off), not a pixel map. Per-pixel control has to go through
the real-time protocol, which is what this bridge does.

## Quick start (Windows)

```
py -m pip install -r requirements.txt
py run.py                      # or double-click run.bat
```

Open http://localhost:5000, add a controller by IP, *Pull map*, *Patch all*, then export the
pixel map for the desk. `py run.py --host 0.0.0.0` exposes the UI to the LAN. Allow
`python.exe` through Windows Firewall when prompted (Private networks).

Linux/macOS: `python3 run.py`.

## Workflow with Eos

1. **Add controllers** by IP. The bridge reads LED count, profile and port layout.
2. **Pull map** on each: fetches the camera layout stored on the controller (`layout/full`).
   Or upload a CSV with `index,x,y,z` columns.
3. **Patch all, new universe each**: every controller gets contiguous universes, 170 pixels
   (510 channels) per universe, ordered by the controllers' group offset when they report one.
4. **Export for Eos**: pick a flatten mode for 3D maps (front, top, side, or unwrap around the
   vertical axis, which turns a tree into a rectangle), preview the grid, download the
   pixel map CSV. On the desk: File → Import → CSV, include Pixel Maps.
5. Set Eos's sACN output to reach the bridge (unicast to the bridge's IP if Eos is on
   another VLAN) and play the pixel map. The bridge's live map should match the wall.

### Linking pixels to fixtures

- **Address + profile** (default): the CSV carries `ADDRESS` (Eos absolute, 512 per universe)
  plus `MANUFACTURER` / `FIXTURE_TYPE`, and Eos creates address-type pixels. If Eos can't find
  the named profile it silently uses Generic Dimmer, so set the two strings to a profile that
  exists in your library (export any small pixel map from Eos to see the exact names).
- **Channel**: pixels reference channels you've already patched. The *patch sheet CSV* lists
  channel, absolute address, port/offset, DDP byte and a label (controller, port, LED) per
  pixel, for Eos's CSV patch import or for checking against the desk.

The bridge packs 510 channels per universe so no pixel straddles a universe boundary; the
export converts that to Eos absolute addresses and refuses if any pixel would straddle.

## Across a VLAN / firewall

Controllers are addressed by IP; nothing depends on broadcast discovery. Open:

- bridge PC → each controller: **TCP 80** and **UDP 7777**, outbound only.
- Eos → bridge PC: **UDP 5568** (sACN), or **UDP 4048** for DDP sources.

Multicast sACN doesn't cross VLANs without IGMP/PIM. Untick *Join multicast groups* and set
the sender to unicast to the bridge's IP.

## What it does while running

- sACN on UDP 5568 (multicast or unicast, per-universe priority, stream-terminated flag
  honoured) and DDP on UDP 4048 run at once; each controller is patched to one of them.
- A fixed-rate output loop (30 fps default) builds each controller's frame from the input
  buffers and sends real-time datagrams, fragments interleaved across controllers. All HTTP
  (login, `rt`, restore) runs on its own thread, so one dead controller never stalls the rest.
- A controller enters `rt` when data arrives for its range, `rt` is re-asserted every 30 s,
  tokens re-login on 401, and after the idle timeout the controller returns to the mode it
  was in (movie, effect, playlist) so a stopped show doesn't leave the installation dark.
- Click any LED on the live map to flash it and read its controller / port / LED.

## Tools (standalone, stdlib only)

| Script | Purpose |
|---|---|
| `tools/twinkly_test.py` | `discover`, `info`, `rt-http` (one frame over HTTP), `rt` (UDP stream) |
| `tools/twinkly_pixels.py` | per-pixel demos: port colours, single pixel, chase, rainbow, fps probe |
| `tools/twinkly_layout.py` | pull one controller's map to JSON / CSV / SVG and a single-controller Eos pixel map CSV |

## API

```
GET  /api/status
POST /api/settings                       {"sacn": {...}, "ddp": {...}, "output": {...}}
GET  /api/controllers
POST /api/controllers                    {"ip": "10.0.0.5", "universe": 1, "channel": 1, "source": "sacn"}
PUT  /api/controllers/<id>               {"source": "ddp", "ddp_start": 1, "universe": 9, "channel": 1,
                                          "input_order": "RGBW", "enabled": true, "name": "Tree A"}
DELETE /api/controllers/<id>
POST /api/controllers/<id>/connect
POST /api/patch/auto                     {"mode": "aligned"|"packed", "start_universe": 1, "order": "group"|"added"}
POST /api/controllers/<id>/map/pull
POST /api/controllers/<id>/map/csv       multipart file= (columns index,x,y,z)
DELETE /api/controllers/<id>/map
GET  /api/controllers/<id>/map.csv
GET  /api/map                            points per controller
GET  /api/frames                         last frame per controller, base64
POST /api/controllers/<id>/test          {"mode": "pixel"|"solid"|"off"|"stop", "index": 10, "rgb": [255,0,0], "seconds": 3}
GET  /api/export/eos_pixelmap.csv        ?pixelmap=1&flatten=xy|xz|zy|unwrap&arrange=shared|tiled|separate
                                          &width=0&mode=address|channel&channel_base=1&manufacturer=Generic&fixture_type=RGB
GET  /api/export/eos_patch.csv           same parameters
GET  /api/export/eos_preview.svg         same parameters
```

Config lives in `bridge_config.json` next to `run.py` (override with `--config` or
`TWINKLY_BRIDGE_CONFIG`).

## Development

```
py -m pip install -r requirements-dev.txt
py -m pytest -q
```

`tests/mock_controller.py` is a fake Twinkly Pro assembled from packet captures of a real
TWPROCTRLPLC21 (fw 4.1.0): v2 REST endpoints and a real-time receiver that validates every
datagram. The tests run the whole bridge against it, send sACN and DDP, and check the bytes
that reach the "controller". To try the UI against the mock by hand:

```
set MOCK_HTTP_PORT=80 && py tests\mock_controller.py     # then add controller 127.0.0.1
py tests\senders.py                                       # 3 s of sACN: LED 0 red, 501 green, 1249 blue
```

Protocol notes from the captures are in [docs/protocol.md](docs/protocol.md).

## Status and roadmap

Working against the mock and a single real TWPROCTRLPLC21. Not yet verified on hardware:
multi-controller groups (ordering by `group.offset`), RGBW controllers, consumer (v1)
firmware, the unwrap orientation on a real 3D-mapped tree.

- [ ] Group-aware map pull (one click for every controller in a group)
- [ ] Movie upload, so cues can trigger stored content via the Pro's Art-Net profile
- [ ] sACN sync packets
- [ ] Windows service wrapper / Linux unit

## Credits

The real-time datagram format and the consumer REST API were first documented by the
[xled](https://github.com/scrool/xled) project; the approach of importing a controller's
camera map as a model came from xLights' Twinkly support. Eos CSV format from ETC's
[Pixel Map Editing and Importing Through CSV](https://support.etcconnect.com/ETC/Consoles/Eos_Family/Software_and_Programming/Pixel_Map_Editing_and_Importing_Through_CSV).

## License

MIT. Twinkly and Twinkly Pro are trademarks of their owners; Eos is a trademark of ETC. This
project is not affiliated with either and relies on undocumented behaviour that may change.
