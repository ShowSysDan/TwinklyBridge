# Changelog

Every change the user gets is its own version: bump `bridge/__init__.py` and
`pyproject.toml` together and add a section here (`test_version_matches_everywhere`
fails if they disagree). Semver: patch = fix, minor = feature, major = breaking.

## [0.11.1] - 2026-11-05

- Frozen-camera check made safe: identical frames only count when the picture isn't nearly
  black (in a dark room the all-off photo can come out solid zeros from noise reduction,
  which 0.11.0 could mistake for a frozen camera and give up), and a camera that delivers no
  new frame for 3 x 400 ms now counts as frozen too.

## [0.11.0] - 2026-11-05

From three real captures (TWPROCTRLPLC21, iPhone, lit warehouse):

- **Frozen camera found and fixed.** In those captures the all-off, all-on, red, green and
  blue photos were byte-identical, and identical across two captures a minute apart: the
  iPhone's camera feed had frozen after the first view (what showed as "the camera view
  disappears"), so later views found almost nothing. Now: a real camera never gives two
  byte-identical frames, so three in a row, or an ended camera track, reopen the camera and
  retake; a frame that never arrives no longer hangs a capture. If LEDs on and off look the
  same right after the all-off photo, the capture stops at once with a clear message
  instead of running every pattern (the walk-around then measures afresh).
- **No camera lock on iPhone / iPad** (the likely trigger: asking iOS to lock exposure /
  focus can stall its camera; stabilising already handles drift). Elsewhere the lock is
  undone if the camera stops.
- **Memory**: photos are taken at most ~1 megapixel (720 x 1280 upright) and one buffer is
  reused instead of a new 20 MB one per photo; the walk-around frees each view's photos once
  sent.
- **Uploads that finish**: photos go to the bridge as JPEG (about a tenth of a PNG); the
  walk-around sends a view's photos before telling you to walk on; the bridge refuses an
  empty (cut-off) upload instead of saving a 0-byte file. (The captures had 1, 6 and 7 of 29
  files.)
- **Mapping with the room lights on**: each capture is decoded by brightness and by
  green-ness (how much greener than red and blue a pixel is). The coding patterns are pure
  green and a warehouse's walls, concrete and tinsel aren't, so green-ness picks the LEDs out
  of a lit room where brightness barely separates them (synthetic warehouse: 947 LEDs
  against 733). The better of the two is kept; the result says which and what each found.
- **Room brightness**: the aim view, the capture result and the walk-around's "hardly any
  LEDs found" say how bright the room is with the LEDs off (the captures: 144 of 255) and
  what helps.

## [0.10.2] - 2026-11-05

- The LED level's flicker test compares LED light per 16 x 16 tile instead of pixel by
  pixel. A hand moving a fraction of a pixel between its two photos changed single pixels a
  lot (LEDs are tiny dots) and read as flicker, so a drifting phone picked a dimmer level for
  no reason; flicker (PWM caught by a rolling shutter) swings whole bands of rows, which the
  tiles still see.

## [0.10.1] - 2026-11-05

- Fix: every capture stopped at "Choosing the LED level" with "Assignment to constant
  variable" (0.10.0's flicker test reused the loop's name for the light positions).

## [0.10.0] - 2026-11-05

- **Stabilising: hand-held drift is corrected instead of failing the capture.** The all-on
  photo shows where the lights are; every other photo lights a subset of the same LEDs, so
  sliding it until its lit pixels land on the all-on photo's gives how far the phone drifted
  (quarter-size search, then +-4 px at full size, then a fraction of a pixel). Each photo is
  shifted back before decoding. Tested: photos drifting up to 12 px each find all 1,200 LEDs
  within 0.13 px (none without stabilising); a phone drifting smoothly by 15 px during a real
  capture in the browser found all 1,250.
- **"Phone moved" only when it can't be corrected**: the start and end all-on photos still
  disagree after lining up (the phone turned, not just drifted), or more than four photos
  couldn't be lined up. The motion sensor alone no longer decides. The result says how far
  the phone drifted when it was corrected.
- Lining up is also used where photos are compared during a capture: the stale-photo check
  (a drifting hand could make a stale photo look new) and the LED level's flicker test (drift
  looked like flicker and pushed the level down for no reason).
- **The edges of the lights** are shown on the live aim view as a yellow box around
  everywhere the LEDs show up.
- Per-photo drift is in the capture notes (`log.stabilise` in capture.json).

## [0.9.2] - 2026-11-05

Guided walk-around fixes ("hardly any LEDs found", camera view disappearing):

- The guidance panel no longer sits on top of the camera picture (on a phone held upright the
  picture is short, and the panel covered most of it); it is below it now.
- The live detection overlay (green = LEDs seen, red = clipped, edge warning) runs while
  you walk between captures, and after a failed capture for a few seconds to re-frame; its
  readout is in the guidance panel too.
- **Camera lock checked and undone if it hurts.** Locking exposure / focus / white balance
  now keeps the camera's current values where the browser reports them, and compares the
  LEDs before and after: if locking changed the picture (a phone jumping to a default
  exposure or focus makes it dark or blurred: few or no LEDs found), it unlocks and carries
  on. The capture notes say what happened (`locked` in capture.json, and on the result).
- The walk's first capture measures the camera lag and LED level afresh instead of reusing
  values from an earlier single capture; after a failed capture it measures again.
- "Hardly any LEDs found" now says how many, and the LED level, lag and lock used.

## [0.9.1] - 2026-11-05

- **Clear map** button on each controller card (when it has a map), with a confirmation;
  the controller then shows "no map" until one is pulled, captured, made from ports or
  uploaded. (The API was already there: `DELETE /api/controllers/<id>/map`.)
- The desk page's *Camera mapping* panel lists the camera views kept for building a map and
  can **discard** them (the phone page already could), so a new walk-around starts afresh.
- Fix: elements hidden by the page (the live map once no controller has a map, and others)
  stayed visible, because a CSS display rule beat the `hidden` attribute.

## [0.9.0] - 2026-11-05

- **Guided walk-around**: one tap maps a 3D piece all the way round. The phone captures a
  view, keeps it (coverage on the LEDs, photos to the bridge), then directs you with the
  compass and tilt sensor: "Walk to your right: 28 of 45 degrees" with a gauge, "Too far:
  step back", "Hold the phone upright / tilt the top towards you", and captures by itself
  as soon as the phone is at the next angle, upright and steady for a moment. Eight views
  (360 degrees) or *Finish & build* whenever you like, then the 3D map. Each view's angle is
  the compass reading at the capture, not the target. Later views reuse the first one's
  camera lag, wait and LED level (about 16 s each instead of 23). Without a compass it asks
  for a tap (*I'm there*) at each stop. *Capture one view* is still there for flat pieces.
- The phone still has to stand still during each capture: an LED's number is read from the
  same pixels across about 25 photos, so it walks, stops, holds, walks.

## [0.8.0] - 2026-11-05

- **Live aim view.** As soon as the camera opens, the bridge blinks the LEDs (all on / off,
  about once a second) and the phone keeps each pixel's brightest and darkest value of the
  last two seconds: the difference is LED light, whatever the camera's lag. The preview is
  overlaid green where the camera sees LEDs and red where they clip, with the share clipped
  and a warning when LEDs reach the edge of the picture (some probably out of frame).
- **Automatic LED level.** Each capture first lights the all-on frame at 255, 160, 96, 56
  and 32 and, at each, compares two photos of the steady frame (if they differ where the
  LEDs are, the PWM dimming flickers on this camera) and counts clipped LED pixels. The
  coding patterns use the brightest level that doesn't flicker and hardly clips; full 255 if
  every dimmed level flickers. The result shows the level and what each level did; the aim
  view then blinks at that level. The bridge takes `"level"` on `POST /api/mapping/step`.
- **Photos stream to the bridge after every capture** (on by default, a checkbox on the
  camera screen), so a capture is always there to download from the desk page.
- Tested with a fake camera lagging 250 ms with 3x gain: without flicker it chose 56 (255,
  160 and 96 clipped 44%), with flicker below 255 it kept 255; all 1,250 LEDs found both times.
- Decoding stays on the phone (fast there; the bridge would need numpy / OpenCV to match);
  the streamed photos are there for processing on the bridge later if it is ever needed.

## [0.7.0] - 2026-11-05

Camera mapping: identifying LEDs on a real camera.

- **Camera lag measured, every photo checked.** Each capture starts by switching the LEDs from
  off to on and timing how long the camera takes to show it (phones add 100-400 ms); the wait
  after each pattern is set from that (x1.3), instead of a fixed 300 ms. Each photo must
  differ clearly from the previous one, or it was taken before the LEDs changed: the phone
  waits and takes it again (up to 4 times) and stops with a clear message rather than
  decoding a stale photo. A stale photo flips a bit for every LED in it and sends them to
  wrong places, which is the "all over the place" result.
  The lag test counts per-pixel brightening (LEDs are small dots: the picture's average
  hardly moves) and starts from a dark picture taken after the camera has had longer than any
  phone's lag to show the LEDs going dark (a still picture can just be a lagging one).
  Tested with a camera lagging 450 ms: measured 440 ms, all 1,250 LEDs found.
- **Coding patterns full-on green (255).** A channel at 255 isn't dimmed by PWM, so the
  phone's rolling shutter can't catch it half off (dark bands across the photo); green is
  what phone sensors see best. The brightness slider now only sets the coverage colours.
- **All-on first, then the camera is locked**: exposure, focus and white balance are set to
  manual where the browser allows (Android Chrome; iOS Safari ignores it).
- **Result dots coloured along each string** (red -> violet from its first LED): a right
  string is one smooth rainbow, a misread LED is the wrong colour. The result also shows the
  measured lag, the wait used, photos retaken and what was locked.
- **Send photos to the bridge** from the result screen: every photo of the capture plus its
  notes go to `instance/captures/` (latest 20 kept); the desk page's *Camera mapping* panel
  lists them for download as a zip. API: `POST /api/mapping/captures/<name>/<file>`,
  `GET /api/mapping/captures`, `GET /api/mapping/captures/<name>.zip`, `DELETE`.
- Tried and dropped: a parity check pattern (in Gray code chain neighbours always differ in
  parity, so it rejected every pixel between neighbouring LEDs: 40% fewer LEDs found on
  dense strings) and finding spots before reading their bits (merges LEDs under ~3 px apart).
  The per-pixel decoder stays; tests now also run it on a harsh synthetic camera (bloom,
  clipping, banding, a floor reflection, more noise, exposure drift): 1,153 of 1,200 found,
  none misplaced.

## [0.6.0] - 2026-11-05

- **3D camera mapping from several views.** Each capture can be kept as a view with the
  angle it was taken from (walking round the object to the right). *Build map* fits views
  from one direction onto each other (zoom, turn, shift) and averages them, or, for views
  from round the object, works out every LED's 3D position: LEDs seen from two directions
  are placed exactly, LEDs seen from one get depth from their neighbours; each view's zoom
  and offset are fitted and typed angles corrected (best with 8 views). Preview from the
  front and from above, *Flip* for a mirrored result, then save. Views are kept in
  `instance/mapping_views.json` so more can be added later. Tested on a sphere (1% of its
  size with 4 views at true angles) and a tree with LEDs hidden in every view.
- **The phone's compass fills in each view's angle** (heading change since the first view);
  tilt and movement are read from the motion sensors and warned about. (LiDAR isn't
  available to web pages.)
- **Coverage on the LEDs between captures**: green = found in this view, blue = found
  before, red = not found yet, to see where to capture next.
- **Smarter gap filling** for obstructed / unseen LEDs, along each string: in 3D, round the
  object (angle, radius, height) at the string's own spiral rate rather than straight
  through it; at a string's end, continuing its last stretch. The vertical axis is found by
  circles fitted per height band (cones and trees narrow upwards), so a hidden side doesn't
  pull it off centre. Filled-in LEDs are remembered and drawn as rings on the live map.
- **Check patterns placed by the map**: `sweep_x`, `sweep_y`, `sweep_z` (bars), `ring`,
  `spin` (a plane round the vertical axis) and `map_rainbow`, with a sweep time; on the phone
  after saving and in the desk Test patterns panel. Several controllers share one box and one
  clock, so a bar crosses them as one piece.
- **Eos export of 3D maps**: Eos pixel maps are flat, so 3D maps default to *Unwrap*: the
  cylinder / cone cut down the back and opened out, front in the middle, width in proportion
  to the way round, height down. New: *Cone, unrolled exactly* (a fan; distances along the
  surface stay true; apex found from how the radius narrows) and *Sphere*.
- The desk live map shows 3D maps in 3D: drag to turn, double-click to reset.
- The phone page's bare address (`https://<bridge>:5121`) opens camera mapping; while more
  views are to come, the port report says "not seen yet" instead of "string ends".

## [0.5.1] - 2026-11-05

Fixes for the phone page not loading:

- **Plain `http://` on the HTTPS port is redirected** to `https://` (301). Safari tries
  plain HTTP when the address is typed without `https://`, and the bridge used to drop
  those connections, so the page "didn't load".
- **HTTPS listens on every interface by default.** Under `run.py` / `run.bat` it followed
  `HOST`, and the `.env` that install.bat creates sets `HOST=127.0.0.1`, so the phone could
  not reach it unless the bridge was started with `--host 0.0.0.0`. `HTTPS_HOST` still
  overrides.
- The desk UI's *Camera mapping* panel shows what is going on: the exact addresses to open,
  where HTTPS is listening, why it is off (e.g. no certificate), and whether the certificate
  is missing one of this machine's IPs. The start-up log prints the phone address(es).
- Tests for the HTTPS listener: TLS, the redirect, stalled connections, a missing
  certificate.

## [0.5.0] - 2026-11-05

- **Camera mapping from a phone** (`https://<bridge>:5121/map`). The bridge lights every
  LED of the chosen controllers in Gray-coded patterns (11 bit / inverse pairs for 1,250
  LEDs, plus black, white, red, green, blue and a closing white: 28 frames, about 15 s); the
  phone photographs each one, works out which LED every camera pixel saw, and sends back one
  position per LED. Several controllers mapped together share one coordinate space. Runs
  entirely in the phone's browser, no app and no new Python packages.
- **String lengths and colour order per port** from the same run: LEDs seen, where each
  string really ends ("string ends at LED 200 of 250 configured"), dark LEDs before the end,
  and which colour the camera saw for red / green / blue ("colours look GRB"). Shown on the
  phone before saving; returned by the API.
- LEDs the camera missed are filled in along their string; mis-decoded LEDs far from both
  chain neighbours are dropped first. A moved camera (white at start vs end) is flagged.
- **HTTPS listener** for the phone on `HTTPS_PORT` (default 5121), inside the bridge process
  (run.py and the service alike), same access controls. TLS handshakes run per connection,
  so a phone sitting on the certificate warning doesn't block anyone.
- `tools/make_cert.py` (called by install.sh and install.bat) makes an iOS-acceptable
  self-signed certificate: 825 days, the machine's names and IPs in subjectAltName,
  serverAuth. Uses Git for Windows' openssl on Windows. `NEW_CERT=1 sudo bash install.sh`
  makes a new one.
- API: `GET /api/mapping/info`, `POST /api/mapping/start | step | save | stop`; page `/map`.
- `Permissions-Policy` now allows the camera for the bridge's own pages.
- Tests: Gray-code frames decode to every LED, gap filling and the port report, the phone
  decoder against a synthetic camera in Node (noise, auto-exposure changes, a GRB port, a
  short string), and a full session through the API to the mock controller.

## [0.4.0] - 2026-11-05

- **Test patterns**, to prove per-pixel control before mapping: a *Test patterns* panel
  runs a pattern on one controller or every enabled one for a set time (sACN is ignored
  while it runs; *Stop all tests* hands back). Patterns: `channels` (R, G, B, W in turn:
  colour order), `ports` (a colour per port, first LED white, last dim: port order and
  lengths), `markers` (LED 0 green, every 100th red, 50th yellow, 10th dim: count along
  the chain), `chase` (a dot with a tail at a set speed), `gradient` (off to full along
  each port), `rainbow` (scrolling), `solid`. Rendered per output frame in under 1 ms for
  1,250 LEDs. RGBW controllers get the W channel for white.
- **Pixel stepper**: light LED *n* for 30 s with -10 / -1 / +1 / +10 buttons; shows its
  port and LED number.
- API: `GET /api/patterns`, `POST /api/test` (several controllers at once),
  `mode: "pattern"` on `POST /api/controllers/<id>/test`. The controller card shows which
  test is running (`test_pattern` in `/api/status`).

## [0.3.2] - 2026-11-05

- Capture of the Twinkly app viewing and camera-editing the map: the app never reads or
  writes a layout on the controller; the map lives in the Twinkly cloud
  (`api.illucere.com`, `illucere-storage` S3). Recorded in docs/protocol.md with the calls
  the app does make, including `GET /xled/v2/device/config` and the
  `DELETE /xled/v2/movies` it sends after a map edit (deletes every stored movie).
- The Pull map 404 message now says the map is in the Twinkly cloud and points to Map from
  ports / Upload CSV. Diagnose also reads `device/config`.

## [0.3.1] - 2026-11-05

- docs/protocol.md: recorded the Diagnose result from the TWPROCTRLPLC21 fw 4.1.0 that
  shows a map in the Twinkly app but answers 2404 on every layout / map URL tried, v2 and
  v1. The controller does not always hold the app's map.

## [0.3.0] - 2026-11-05

- **Diagnose** button on each controller: logs in and GETs every likely map URL
  (`led/layout/full`, `led/layout`, `layout`, `led/map`, `mapping`, ... under v2 and v1,
  plus `gestalt`, `summary`, `movies`), read-only, and shows status, Twinkly result code,
  reply keys and whether coordinates came back, in a box you can copy. For controllers
  that have a map in the Twinkly app but answer 2404 on `led/layout/full`
  (seen on a TWPROCTRLPLC21 fw 4.1.0). Also `POST /api/controllers/<id>/probe`.
- **Map from ports** button: builds a map from the controller's own port layout, one row
  per port, LEDs in chain order (`POST /api/controllers/<id>/map/ports`). Exact pixel order
  and Eos addressing, not physical positions; gives a working pixel map and parameter count
  until a camera map can be pulled.
- Eos export places maps that are already on a whole-number grid (Map from ports, or a CSV
  in cell units) exactly, e.g. 250 x 5 cells, instead of rescaling and nudging them.
- The Pull map 404 message points to Diagnose / Map from ports instead of assuming the
  controller was never mapped.
- Fix: a controller's card did not refresh after pressing one of its buttons (the page
  skipped redraws while any element in the list had focus, not just an input).

## [0.2.1] - 2026-11-05

- **Pull map 404s now say what happened.** When the controller answers 404 to the layout
  request on both `/xled/v2/led/layout/full` and `/xled/v1/led/layout/full`, the error
  names both URLs, the controller's reply, its model / firmware and group mode, and what to
  do (camera-map it in the Twinkly app, pull from the group master, or upload a CSV),
  instead of a bare `HTTP Error 404: Not Found`. The error stays on the controller's card
  until the next successful pull (`map_error` in `/api/status`), and long messages stay on
  screen longer.
- Pull map retries once with a fresh login if the controller says the token expired (401).
- The bridge's own 404s on `/api/` are JSON (`bridge: ...`), and the UI reports any failed
  API call instead of carrying on as if it had worked.

## [0.2.0] - 2026-11-05

Imported into GitHub (ShowSysDan/TwinklyBridge) and brought in line with the other
ShowSysDan show-control apps.

- **Parameter count.** Once a controller's map is pulled (or a CSV loaded), the bridge
  shows how many Eos parameters the pixel map will use: one per colour per mapped pixel
  (3 for RGB, 4 for RGBW). Per controller on its card, a total in the header, a
  per-controller table above *Export for Eos*, in the *Pull map* toast, in the preview SVG
  header for each pixel map, in `/api/status` (`parameters`) and at `/api/parameters`.
- **Linux service.** `install.sh` creates `venv/`, installs the pinned requirements,
  creates `.env` and writes a sandboxed systemd unit (`twinkly-bridge.service`, gunicorn
  with one worker, restart forever). `sudo bash install.sh uninstall` removes it.
- **Windows.** `install.bat` creates `venv\` and installs the requirements; `run.bat`
  uses that venv.
- **Default web port is now 5120** (was 5000, which macOS AirPlay also uses). `HOST` /
  `PORT` in `.env` or `--host` / `--port` change it.
- **Config moved to `instance/bridge_config.json`** (git-ignored, the only folder the
  service may write). An existing `bridge_config.json` next to `run.py` is still used
  until it is moved; `install.sh` / `install.bat` move it.
- **Access control** (all optional, off by default): `ALLOWED_NETWORKS` address
  allowlist, `PANEL_PASSWORD` HTTP Basic auth, cross-site request blocking, JSON-only
  mutating API, Content-Security-Policy and other security headers, 8 MB body limit.
- **Logging** through Python `logging`: console / journald, local syslog (tag
  `twinkly-bridge`, LOCAL0) and an optional remote syslog server (`SYSLOG_HOST`). Every
  operator action (controller added / edited / removed, auto patch, map pulled, settings
  saved) is logged with the client's IP; a controller going into error is logged once.
- `/healthz` for monitoring (no login), `/api/version`, version shown in the UI header.
- `.env.example`; dependencies pinned to exact tested versions (Flask 3.1.3,
  python-dotenv 1.2.4, gunicorn 26.2.0 on Linux).
- The UI's script and styles moved to `bridge/static/` so the CSP can forbid inline
  script. Controller names are escaped in the preview SVG.
- Werkzeug debugger explicitly off in `run.py`.
- Tests: the mock controller reports the last *complete* real-time frame, fixing an
  intermittent failure when a test read a frame that was still arriving.

## [0.1.0] - 2026-11-05

First release, as uploaded: sACN and DDP input, Twinkly Pro real-time output, camera map
pull, live map, auto patch, Eos pixel map / patch sheet export, standalone tools and the
mock controller test suite.
