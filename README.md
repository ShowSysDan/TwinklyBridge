# TwinklyBridge (twinkly-bridge)

Drive **Twinkly Pro** controllers per-pixel from an **ETC Eos** console (or anything else
that speaks sACN or DDP). The bridge logs into each controller over its local HTTP API, puts
it in real-time mode, and streams Eos's sACN output to it over UDP. Its web UI handles
patching, shows the controllers' camera maps lit with the frames going out, counts the
**Eos parameters** each map will use, and exports the map as an **Eos Pixel Map CSV** built
from the bridge's own patch, so the desk and the bridge can't disagree about an address.

Current version: **0.11.0** (see [CHANGELOG.md](CHANGELOG.md))

```
Eos (sACN) --> twinkly-bridge (web UI :5120) --HTTP + UDP 7777--> controller 1  (VLAN B)
                                                               --> controller 2
                                                               --> ...
```

Twinkly Pro's own Art-Net support is a 10-channel "whole controller" fixture (brightness,
mode, R G B W, movie, playlist, on/off), not a pixel map. Per-pixel control has to go through
the real-time protocol, which is what this bridge does.

---

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Install on Linux (service)](#install-on-linux-service), step by step
- [Install on Windows](#install-on-windows), step by step
- [Updating, logs, uninstall](#updating-logs-uninstall)
- [Workflow with Eos](#workflow-with-eos) and the [parameter count](#parameter-count)
- [Across a VLAN / firewall](#across-a-vlan--firewall)
- [Security](#security) and [Configuration (.env)](#configuration-env)
- [Tools](#tools-standalone-stdlib-only), [API](#api), [Development](#development)

---

## Features

| Feature | Summary |
|---|---|
| **sACN and DDP in** | sACN on UDP 5568 (multicast or unicast, per-universe priority, stream-terminated honoured) and DDP on UDP 4048, at the same time; each controller is patched to one of them |
| **Twinkly real-time out** | Fixed-rate output loop (30 fps default), fragments interleaved across controllers; all HTTP on its own thread so one dead controller never stalls the rest |
| **Hands-off modes** | A controller enters `rt` when data arrives for its range, `rt` is re-asserted every 30 s, tokens re-login on 401, and after the idle timeout the controller returns to the movie / effect / playlist it was playing |
| **Camera maps** | *Pull map* reads the 2D / 3D layout stored on the controller (`layout/full`), or upload a CSV |
| **Test patterns** | Channels, ports, index markers, chase, gradient, rainbow, solid, and a pixel stepper, on one controller or all, to prove every pixel answers before mapping |
| **Camera mapping (phone)** | The bridge flashes coded patterns, an iPhone / Android browser photographs them and works out every LED's position, each port's real string length and its colour order. About 15 s |
| **Parameter count** | As soon as a map is in, how many Eos parameters it uses, per controller and in total (see [Parameter count](#parameter-count)) |
| **Live map** | Every LED drawn where it really is, lit with the frames going out; click one to flash it and read its controller / port / LED |
| **Auto patch** | Every controller on contiguous universes (170 RGB pixels / 510 channels each), ordered by group offset |
| **Eos export** | Pixel Map CSV (address + profile, or channel) and a patch sheet CSV; 3D maps flattened front / top / side or unwrapped around the vertical axis |
| **Runs as a service** | `install.sh` sets up a sandboxed systemd service on Linux that restarts forever; `install.bat` / `run.bat` on Windows |
| **Access control** | Optional address allowlist, UI password, cross-site request blocking, security headers |
| **Logging** | Every operator action and every controller fault, to the console / journald, local syslog and optionally a remote syslog server |

---

## Requirements

- **Python 3.10 or newer** with `venv` and `pip`, and **git**.
  - Linux: Debian 12, Ubuntu 22.04+, Raspberry Pi OS Bookworm or later.
  - Windows 10 / 11: Python from [python.org](https://www.python.org/downloads/) and
    [Git for Windows](https://git-scm.com/download/win).
- Everything else comes from `requirements.txt`, pinned to exact tested versions (Flask,
  python-dotenv, and gunicorn on Linux).
- Network: the bridge PC must reach each controller on **TCP 80** and **UDP 7777**, and
  receive **UDP 5568** (sACN) and/or **UDP 4048** (DDP) from the console. The web UI is
  on **TCP 5120**, and the phone page for camera mapping on **TCP 5121** (HTTPS). See
  [Across a VLAN / firewall](#across-a-vlan--firewall).
- `openssl`, to make the HTTPS certificate for the phone: on Linux `sudo apt install openssl`
  (usually already there); on Windows it comes with Git for Windows.
- Linux with systemd for the service install. Windows and macOS run it with `run.py`.

---

## Install on Linux (service)

This is the recommended setup for a permanent install: the bridge starts at boot, restarts
itself if anything goes wrong, and needs nobody logged in.

### 1. Install the system packages

```bash
sudo apt update
```

```bash
sudo apt install -y git python3 python3-venv python3-pip
```

Check the Python version is 3.10 or newer:

```bash
python3 --version
```

### 2. Clone the code into your home directory

Log in as the (ordinary, non-root) account the service should run as, then:

```bash
cd ~
```

```bash
git clone https://github.com/ShowSysDan/TwinklyBridge.git
```

```bash
cd TwinklyBridge
```

### 3. Run the installer

```bash
sudo bash install.sh
```

The script:

1. creates `venv/` and installs `requirements.txt` into it (as your user, not root);
2. creates `.env` from `.env.example` if there isn't one (mode 600);
3. creates `instance/` (mode 700), where the bridge saves its controllers and patch
   (`instance/bridge_config.json`), and a self-signed HTTPS certificate there for the phone
   page (`instance/cert.pem`, `key.pem`; see [Camera mapping](#camera-mapping-phone));
4. writes `/etc/systemd/system/twinkly-bridge.service` for your user and this folder
   (gunicorn, one worker, sandboxed: no privileges, read-only file system except
   `instance/`, restart forever), then enables and starts it;
5. prints the URL, and a reminder if no access control is set.

Options, as environment variables in front of the command:

```bash
PORT=8080 sudo bash install.sh
```

```bash
HOST=10.1.1.5 sudo bash install.sh
```

```bash
HTTPS_PORT=5121 sudo bash install.sh
```

```bash
NEW_CERT=1 sudo bash install.sh
```

`PORT` is the web UI port (default 5120); `HOST` binds the UI (and HTTPS) to one interface
(default `0.0.0.0`, every interface); `HTTPS_PORT` is the phone page's HTTPS port (default
5121, `0` turns it off); `NEW_CERT=1` makes a new certificate, e.g. after the machine's IP
address changed (the phone then warns once more). Ports below 1024 work: the unit then gets
just `CAP_NET_BIND_SERVICE`.

### 4. Open the firewall (if one is on)

With `ufw`:

```bash
sudo ufw allow 5120/tcp comment 'twinkly-bridge web UI'
```

```bash
sudo ufw allow 5568/udp comment 'twinkly-bridge sACN'
```

```bash
sudo ufw allow 4048/udp comment 'twinkly-bridge DDP'
```

```bash
sudo ufw allow 5121/tcp comment 'twinkly-bridge phone (HTTPS)'
```

Outbound traffic to the controllers (TCP 80, UDP 7777) is normally allowed already.

### 5. Check it

```bash
systemctl status twinkly-bridge
```

```bash
curl http://localhost:5120/healthz
```

`/healthz` answers `{"ok": true, "version": "0.11.0", "controllers": 0, ...}`. Then open
`http://<bridge-ip>:5120` in a browser and follow [Workflow with Eos](#workflow-with-eos).

### 6. Lock it down (recommended on any shared network)

Edit `.env` and set `PANEL_PASSWORD` and/or `ALLOWED_NETWORKS` (see [Security](#security)):

```bash
nano ~/TwinklyBridge/.env
```

```bash
sudo systemctl restart twinkly-bridge
```

### Running it by hand (no systemd)

Useful for a quick test, or on macOS:

```bash
python3 -m venv venv
```

```bash
venv/bin/pip install -r requirements.txt
```

```bash
venv/bin/python run.py --host 0.0.0.0
```

Stop the service first (`sudo systemctl stop twinkly-bridge`) if it is installed, or the
two will fight over UDP 5568.

---

## Install on Windows

### 1. Install Python and Git

1. Download Python 3.10 or newer from <https://www.python.org/downloads/> and run the
   installer. Tick **Add python.exe to PATH**. Keep the default *py launcher* option.
2. Download Git from <https://git-scm.com/download/win> and install it with the defaults.

Open a new **Command Prompt** and check both:

```bash
py --version
```

```bash
git --version
```

### 2. Clone the code

Pick a folder that will stay put (the bridge saves its config inside it), e.g. your
user folder:

```bash
cd %USERPROFILE%
```

```bash
git clone https://github.com/ShowSysDan/TwinklyBridge.git
```

```bash
cd TwinklyBridge
```

### 3. Install

```bash
install.bat
```

(or double-click `install.bat` in Explorer). It creates `venv\`, installs the pinned
requirements, and creates `.env` and `instance\`. Safe to run again after an update.

### 4. Run

```bash
run.bat --host 0.0.0.0
```

(double-clicking `run.bat` starts it on `127.0.0.1` only: the UI is reachable from this PC
but not the LAN, and the phone can't reach the camera-mapping page). Leave the window open
while the bridge is in use; closing it stops the bridge. Open <http://localhost:5120>; the
phone page is `https://<this PC's IP>:5121/map`.

To always listen on the LAN without typing the option, set `HOST=0.0.0.0` in `.env`.

### 5. Allow it through Windows Firewall

The first time it runs, Windows asks whether to allow `python.exe`: tick **Private
networks** and allow. If the console's network is classed *Public*, or the prompt was
dismissed, add the rules by hand from an **administrator** PowerShell:

```bash
New-NetFirewallRule -DisplayName "twinkly-bridge sACN" -Direction Inbound -Protocol UDP -LocalPort 5568 -Action Allow
```

```bash
New-NetFirewallRule -DisplayName "twinkly-bridge DDP" -Direction Inbound -Protocol UDP -LocalPort 4048 -Action Allow
```

```bash
New-NetFirewallRule -DisplayName "twinkly-bridge web UI" -Direction Inbound -Protocol TCP -LocalPort 5120 -Action Allow
```

```bash
New-NetFirewallRule -DisplayName "twinkly-bridge phone (HTTPS)" -Direction Inbound -Protocol TCP -LocalPort 5121 -Action Allow
```

### 6. Start it automatically (optional)

To start the bridge whenever you log in, create a scheduled task (run from the
`TwinklyBridge` folder in Command Prompt):

```bash
schtasks /create /tn "twinkly-bridge" /sc onlogon /tr "\"%CD%\venv\Scripts\python.exe\" \"%CD%\run.py\" --host 0.0.0.0"
```

Remove it again with `schtasks /delete /tn "twinkly-bridge" /f`.

---

## Updating, logs, uninstall

### Update (Linux)

```bash
cd ~/TwinklyBridge
```

```bash
git pull
```

```bash
sudo bash install.sh
```

The installer re-installs the requirements and restarts the service. `.env` and
`instance/bridge_config.json` (controllers, patch, maps, settings) are kept.

### Update (Windows)

Close the bridge window, then in the `TwinklyBridge` folder:

```bash
git pull
```

```bash
install.bat
```

and start it again with `run.bat`.

### Logs

```bash
sudo journalctl -u twinkly-bridge -f
```

```bash
journalctl -t twinkly-bridge -f
```

The first is the service output (gunicorn and the bridge); the second is the bridge's
entries in the local syslog (LOCAL0, tag `twinkly-bridge`). On Windows the log is the
`run.bat` window. Set `SYSLOG_HOST` in `.env` to send every line to a remote syslog server
as well. What gets logged:

| Level | Events |
|---|---|
| INFO | Start-up, controller added / edited / removed, auto patch, map pulled or CSV loaded (with its parameter count), settings saved, each with the client IP; a controller recovering |
| WARNING | A controller going into error (login, `rt`, restore; logged once per new error, not every retry), a failed map pull, refused HTTP requests, access controls left open |

### Back up

Everything the bridge knows is in `instance/bridge_config.json` (plus `.env`). Copy those
two files.

### Uninstall (Linux)

```bash
sudo bash ~/TwinklyBridge/install.sh uninstall
```

This stops and removes the service and leaves the folder; delete `~/TwinklyBridge` by hand
if you no longer need it. On Windows, delete the scheduled task (if you made one) and the
folder.

### Moving from 0.1.0

0.1.0 kept `bridge_config.json` next to `run.py` and served the UI on port 5000. 0.2.0 keeps
using that file until it is moved into `instance/` (both installers move it for you) and
serves the UI on **5120**; set `PORT=5000` in `.env` (Windows / `run.py`) or run
`PORT=5000 sudo bash install.sh` to keep the old port.

---

## Workflow with Eos

1. **Add controllers** by IP. The bridge reads LED count, profile and port layout.
2. **Pull map** on each: fetches the camera layout stored on the controller (`layout/full`).
   Or upload a CSV with `index,x,y,z` columns, or press **Map from ports** for a map built
   from the controller's port layout (one row per port, LEDs in chain order: exact
   addressing, not physical positions). The [parameter count](#parameter-count) appears
   as soon as a map is in.
   **Clear map** on a controller's card removes its map (download the CSV first if you might
   want it back).
3. **Patch all, new universe each**: every controller gets contiguous universes, 170 pixels
   (510 channels) per universe, ordered by the controllers' group offset when they report one.
4. **Export for Eos**: Eos pixel maps are flat, so a 3D map is flattened for the export
   (the bridge keeps the 3D map). 3D maps default to **Unwrap**: the cylinder or cone cut
   down the back and opened out, front in the middle, width in proportion to the way round
   and height down, so a tree becomes a rectangle. **Cone, unrolled exactly** opens it into
   a fan instead (distances along the surface stay true; needs more cells); **Sphere** uses
   longitude and latitude; front, top and side views are there too. Preview the grid,
   download the pixel map CSV. On the desk: File → Import → CSV, include Pixel Maps.
5. Set Eos's sACN output to reach the bridge (unicast to the bridge's IP if Eos is on
   another VLAN) and play the pixel map. The bridge's live map should match the wall.

### Camera mapping (phone)

Maps where every LED is, from a phone's camera, when the controller has no map to pull
(Twinkly Pro keeps the app's map in the cloud): flat pieces from one capture, 3D pieces
(trees, columns, spheres) from captures all the way round. Each capture also measures every
port's real string length and checks its colour order.

1. **Open the page on the phone**: `https://<bridge-ip>:5121` (the desk UI's *Camera
   mapping* panel shows the exact address). The bridge's certificate is self-signed, so the
   first time the phone warns: on iPhone tap *Show Details*, *visit this website*, *Visit
   Website*. Phones only allow a web page to use the camera over HTTPS, which is why this
   page is on its own port.
2. **Tick the controllers** to map. Map controllers that share a camera view together: they
   end up in one coordinate space, so the Eos export can lay them out as one map.
3. **Open the camera** and allow camera and motion access (motion gives the compass and tilt
   readouts). Frame the whole installation and hold the phone still: a tripod, or leaned
   against something. Darken the room if you can.
   While you aim, the LEDs blink and the preview shows green where the camera sees them,
   red where they are too bright (clipped), and warns if LEDs reach the edge of the picture.
4. **Capture.** The phone picks the LED level (it tries 255 down to 32 and takes the brightest
   that neither flickers on this camera nor clips: dimming LEDs is PWM, which a phone's
   rolling shutter can catch as stripes), first times the camera (LEDs off, then on: how long until the
   camera shows it) and sets its wait from that, then the LEDs flash 28 full-green patterns
   (about 15-25 s for 1,250 LEDs, depending on the camera); keep still until it says
   *Working it out*. Every photo is checked against the one before; one taken before the
   LEDs changed is taken again. A hand-held phone may drift a little: every photo is lined up
   with the all-on photo (using the LEDs themselves) before decoding, and *the phone moved* is
   only reported when it turned or drifted too far to correct.
5. **Check the capture**: the photo with a dot on every LED found, coloured by port, and a
   table per port:

   | Column | Meaning |
   |---|---|
   | Set / Seen | LEDs configured on the port / LEDs the camera found |
   | Ends at | the last LED seen: the string's real length. *string ends at LED 200 of 250 configured* means 50 configured LEDs don't exist (or are out of frame) |
   | Colours | what the camera saw when the bridge sent red, green, blue: `RGB` is right; `GRB` (*red sent, green seen*) means those strings take a different colour order |

   Dots run red to violet along each string from its first LED: a right string is one
   smooth rainbow; a dot in the wrong colour is a misread LED. If a capture looks wrong,
   **Send photos to the bridge**: every photo goes to `instance/captures/`, and the desk
   page's *Camera mapping* panel offers them as a zip, to see what the camera saw. (They are
   sent after every capture anyway while *Send the photos to the bridge after each capture*
   is ticked, as it is by default.)
6. **Keep this view.** The installation now shows what has been found: **green** in this
   view, **blue** in earlier views, **red** not found yet.
   **Walk around (guided)** does steps 4 to 8 by itself for a 3D piece: it captures and keeps
   a view, then directs you round with the compass and tilt sensor ("Walk to your right: 28
   of 45 degrees", "Too far: step back", "Hold the phone upright") and captures again by itself
   once the phone is at the next angle, upright and still; after eight views (or *Finish &
   build*) it builds the map. The phone must stand still while each view is captured (about
   16 s after the first), because each LED's number is read from the same pixels across about
   25 photos. Without a compass it asks you to tap *I'm there* at each stop.
7. Or by hand: for a 3D piece, **walk about 45 degrees further round to your right**, keeping the same
   distance and height, point the phone at the middle of the piece again and *Capture another
   view*; repeat until little red is left. The angle fills itself in from the phone's compass
   (heading change since the first view) and can be typed or picked instead. 8 views all
   round is best (they also correct a compass that is a few degrees out); 4 is the minimum.
   For a flat piece one view is enough; more views from the front just fill gaps.
8. **Build map** combines the views: views from about the same direction are fitted onto
   each other (zoom, turn, shift) and averaged (2D); views from different directions give
   each LED's 3D position (each LED seen from two directions is placed exactly, one seen
   from one direction gets its depth from its neighbours). The preview shows the map from
   the front and from above, coloured along the chain. If it looks mirrored compared with the
   real object (you walked left), press **Flip**.
9. **Save map**, then **check it on the installation**: *Left to right*, *Top to bottom*,
   *Front to back*, *Spin around*, *Ring from centre* and *Rainbow across* run bars and
   planes placed by the map. On a right map the bar crosses the real object smoothly and
   straight; stray LEDs show where it is off. The same patterns are in the desk UI's *Test
   patterns* panel (group *On the map*).

**Obstructed or unseen LEDs** are filled in along their string, assuming the string carries
on the way it was going: in 3D, round the object (angle, distance from the axis and height
change smoothly, at the string's own spiral rate either side of the gap) rather than straight
through it; at a string's end, by continuing its last stretch. The vertical axis comes from
circles fitted to the LEDs seen at each height, so a hidden side doesn't pull it off centre.
The live map draws filled-in LEDs as rings, so you can see which positions are guesses.

Views are kept (in `instance/mapping_views.json`) until you discard them, so you can come
back and add views later; starting with a different set of controllers starts afresh.

Good to know: the 3D model assumes the phone is upright and far enough away that the piece
isn't distorted by perspective: stand back and zoom in rather than standing close (the page
warns when the phone tilts more than 15 degrees or moves during a capture). The phone's
LiDAR isn't available to web pages, only to native apps, so depth comes from the views.

If the phone page doesn't load, work down this list. The desk UI's *Camera mapping* panel
shows the addresses, where HTTPS is listening, and why it is off if it is:

- **Panel says "HTTPS is off: no certificate"**: run `install.bat` (Windows) or
  `sudo bash install.sh` (Linux) again after updating, or `python tools/make_cert.py`, then
  restart the bridge. On Windows the certificate needs Git for Windows' `openssl`.
- **The phone can open `http://<bridge-ip>:5120` but not `:5121`**: the firewall blocks TCP
  5121. Add the rule from the install steps above (Windows: `New-NetFirewallRule ...
  -LocalPort 5121`; Linux: `sudo ufw allow 5121/tcp`).
- **The phone can't open `http://<bridge-ip>:5120` either**: it isn't on a network that
  reaches the bridge (guest Wi-Fi, another VLAN), or the bridge was started with
  `HOST=127.0.0.1`; the HTTPS port listens on every interface regardless.
- **Safari says it can't establish a secure connection, with no way to continue**: make a
  fresh certificate (`NEW_CERT=1 sudo bash install.sh`, or `python tools/make_cert.py
  --force`) and restart; the panel warns when the machine's IP is missing from it.
- Use the address the panel shows, with the `https://` (typing it without now redirects).

Lit rooms: mapping works with the lights on. Each capture is read both by brightness and by
the LEDs' green colour (white walls, grey concrete and copper tinsel aren't green, however
bright), and the better result is kept. A darker room still finds more LEDs, and shiny
surfaces near the LEDs (tinsel) reflect their light; the phone shows how bright it measures
the room.

Tips: keep the phone still; that matters more than anything else. The
wait after each pattern is measured per capture (camera lag x 1.3); if the result shows many
*photos retaken* or stops with *the camera isn't keeping up*, raise *Shortest wait after
each pattern*. If dots are scattered or strings aren't smooth rainbows, *Send photos to the
bridge* and look at (or share) the zip: the photos show what the camera actually saw.

How it works: every LED gets a number, written in Gray code, one bit per pattern and each
pattern followed by its inverse. The phone reads each camera pixel's bits back as an LED
number, then takes the centre of each LED's pixels. Neighbouring LEDs differ in one bit, so a
pixel blurred between two decodes to one of them, never to a stranger; the inverse pairs
keep the camera's auto-exposure steady and give each bit its own confidence. All the image
work happens in the phone's browser.

### Test patterns: prove every pixel first

Before mapping or programming, check the bridge really controls each pixel. In the
**Test patterns** panel pick a controller (or *All enabled*), a pattern and how long to run
it, and press *Start pattern*. A running test overrides sACN for those controllers;
*Stop all tests* hands them back. The live map shows exactly what is being sent.

| Pattern | What to look for on the installation |
|---|---|
| `channels` | The whole chain red, then green, then blue, then white, 1.5 s each. A wrong order means the controller's colour order / LED profile is set wrong |
| `ports` | Each port one colour (red, green, blue, orange, magenta, ...), its first LED white and its last LED dim grey. Shows port order, where each string starts and ends, and any LED that stays dark |
| `markers` | LED 0 green, every 100th red, every 50th yellow, every 10th dim white: count along the chain to match index to physical LED |
| `chase` | One dot (in the chosen colour) with a tail running down the whole chain at *Speed* LEDs per second; watch for skips, doubles or stalls |
| `gradient` | Each port fades from off to full along its length |
| `rainbow` | A scrolling rainbow: every LED a different colour on every frame |
| `solid` | Every LED in the chosen colour |

The **Pixel stepper** lights one LED (index in chain order, from 0) for 30 s; -10 / ◀ /
▶ / +10 move it, and the readout gives its port and LED number, e.g. `251 = port 1, LED 2`.
The *Flash red* / *Blackout 3 s* buttons on each card and clicking an LED on the live map
still work too.

### If Pull map fails

The error stays on the controller's card and names every URL tried and the controller's
reply. The common one:

- **`/xled/v2/led/layout/full -> 404 {"result":{"code":2404}}`** (and the v1 URL): the
  controller is reachable and logged in but did not return a layout there. On Twinkly Pro
  firmware 4.x (TWPROCTRLPLC21 fw 4.1.0) this is normal: a packet capture shows the app
  keeps the map in the Twinkly cloud and never stores it on the controller, so there is
  nothing to pull (details in [docs/protocol.md](docs/protocol.md)). Press **Diagnose**: it
  tries every likely map URL, read-only, and lists what answered; a line marked
  `COORDINATES` is where the map is. Without one, use **Map from ports** or **Upload CSV**.
- **Mapping in the Twinkly app deletes the controller's movies.** After a camera-map edit
  the app sends `DELETE /xled/v2/movies`. Re-upload movies from the app afterwards, or the
  bridge's *restore previous mode when idle* has no movie to go back to. If the controller is in a group, also try
  *Pull map* on the group's master.
- **`cannot log in`**: the controller is unreachable on TCP 80 from the bridge (VLAN /
  firewall), or it isn't a Twinkly.
- **`bridge: ...`**: the error came from the bridge itself, not the controller (e.g. the
  page is stale after the controller was removed): reload the page.

To see exactly what the controller says, from the bridge PC:

```bash
curl -i http://<controller-ip>/xled/v2/gestalt
```

A JSON reply with `number_of_led` means the controller's API is reachable from there.

### Parameter count

Once a controller has a map (pulled from the Twinkly or loaded from CSV) the bridge counts
the parameters the Eos pixel map will use: **one per colour per mapped pixel**, so 3 for an
RGB pixel and 4 for RGBW. Each parameter is one DMX address, so this is also the number of
outputs the pixel map patches on the desk; check it against your console's output /
parameter licence before importing. Only controllers with a map count, and only as many
LEDs as the map covers, which is exactly what the export writes.

It is shown:

- on each controller's card: `1,250 pixels × 3 (RGB) = 3,750 parameters`;
- in the header: `mapped 3,750 parameters` (all mapped controllers);
- above **Export for Eos**: the total, plus a table of pixels, parameters and universes
  per controller;
- in the *Pull map* / *Upload CSV* message, and in the header of each pixel map in
  *Preview grid*;
- in the API: `parameters` on each controller in `/api/status` and `/api/controllers`, the
  totals as `parameters` in `/api/status`, and on their own at `/api/parameters`:

```
{"controllers": 2, "pixels": 2500, "parameters": 7500, "universes": 16,
 "per_controller": [{"name": "C10", "pixels": 1250, "bpp": 3, "parameters": 3750,
                     "universes": [1, 2, 3, 4, 5, 6, 7, 8]}, ...]}
```

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

---

## Across a VLAN / firewall

Controllers are addressed by IP; nothing depends on broadcast discovery. Open:

- bridge PC → each controller: **TCP 80** and **UDP 7777**, outbound only.
- Eos → bridge PC: **UDP 5568** (sACN), or **UDP 4048** for DDP sources.
- operators' browsers → bridge PC: **TCP 5120** (web UI), and the phone used for camera
  mapping → bridge PC: **TCP 5121** (HTTPS).

Multicast sACN doesn't cross VLANs without IGMP/PIM. Untick *Join multicast groups* and set
the sender to unicast to the bridge's IP.

---

## Security

The bridge normally lives on a closed show network, so every control is optional and off
by default; the service log says which are still open at each start. Set them in `.env`
and restart.

| Control | How |
|---|---|
| Address allowlist | `ALLOWED_NETWORKS=10.1.1.0/24, 192.168.10.5`: every other address gets 403 for every page and API call |
| UI password | `PANEL_PASSWORD=...` (user `PANEL_USER`, default `twinkly`): HTTP Basic auth on the UI and `/api/`. `/healthz` stays open for monitoring |
| Cross-site blocking | Always on: a request a browser marks as cross-site is refused, so a web page in an operator's browser can't repatch the bridge or flash a controller |
| JSON-only API | Always on: `POST` / `PUT` / `DELETE` on `/api/` take JSON (the map CSV upload is the one exception), which an HTML form can't send |
| Headers | Content-Security-Policy with no inline script, no framing, no MIME sniffing; API responses never cached; 8 MB request limit |
| Service sandbox | `NoNewPrivileges`, empty capability set, `ProtectSystem=strict` (writable: `instance/` only), `PrivateTmp`, `PrivateDevices`, syscall filter |

Basic auth sends the password in the clear over HTTP; use it on a show network, not across
the internet. The Twinkly controllers themselves have no authentication beyond the
challenge login the bridge performs, so keep them on their own VLAN.

---

## Configuration (.env)

Created from `.env.example` by the installers. Controllers, patch, sACN and output settings
are set in the web UI and stored in `instance/bridge_config.json`.

| Variable | Default | Description |
|---|---|---|
| `HOST` / `PORT` | `127.0.0.1` / `5120` | Web UI bind address and port for `run.py`. The Linux service takes them from `install.sh` instead |
| `PANEL_PASSWORD` | *(unset)* | HTTP Basic auth password for the UI and `/api/`; unset = no login |
| `PANEL_USER` | `twinkly` | Username for the above |
| `ALLOWED_NETWORKS` | *(unset)* | Comma-separated IPs / CIDRs allowed to make any HTTP request; unset = any |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `SYSLOG_HOST` | *(unset)* | Remote syslog server for every log line |
| `SYSLOG_PORT` / `SYSLOG_PROTO` / `SYSLOG_FACILITY` | `514` / `udp` / `local0` | Remote syslog details (facility also applies to local syslog) |
| `HTTPS_PORT` | `5121` | HTTPS port for the phone (camera mapping); `0` = off. The Linux service takes it from `install.sh` |
| `HTTPS_HOST` | `0.0.0.0` | Bind address for HTTPS. Every interface by default, even when `HOST` keeps the web UI on `127.0.0.1`, because the phone has to reach it |
| `TLS_CERT` / `TLS_KEY` | `instance/cert.pem` / `instance/key.pem` | Certificate for HTTPS (made by the installers; replace with one from `mkcert` or your own CA to avoid the warning) |
| `TWINKLY_BRIDGE_CONFIG` | `instance/bridge_config.json` | Where the bridge saves its config (`--config` on `run.py` overrides it) |

---

## Tools (standalone, stdlib only)

| Script | Purpose |
|---|---|
| `tools/twinkly_test.py` | `discover`, `info`, `rt-http` (one frame over HTTP), `rt` (UDP stream) |
| `tools/twinkly_pixels.py` | per-pixel demos: port colours, single pixel, chase, rainbow, fps probe |
| `tools/twinkly_layout.py` | pull one controller's map to JSON / CSV / SVG and a single-controller Eos pixel map CSV |

---

## API

All under the same login / allowlist as the UI. Mutating calls take JSON.

```
GET  /healthz                            {"ok": true, "version", "controllers", "online", "streaming", "sacn_listening"}; no login
GET  /api/version
GET  /api/status                         receivers, output fps, controllers, parameter totals, settings
GET  /api/parameters                     parameter count: totals and per controller
POST /api/settings                       {"sacn": {...}, "ddp": {...}, "output": {...}}
GET  /api/controllers
POST /api/controllers                    {"ip": "10.0.0.5", "universe": 1, "channel": 1, "source": "sacn"}
PUT  /api/controllers/<id>               {"source": "ddp", "ddp_start": 1, "universe": 9, "channel": 1,
                                          "input_order": "RGBW", "enabled": true, "name": "Tree A"}
DELETE /api/controllers/<id>
POST /api/controllers/<id>/connect
POST /api/patch/auto                     {"mode": "aligned"|"packed", "start_universe": 1, "order": "group"|"added"}
POST /api/controllers/<id>/map/pull      -> {"points", "source", "pixels", "bpp", "parameters", "totals"}
POST /api/controllers/<id>/map/ports     map from the port layout: one row per port, LEDs in chain order
POST /api/controllers/<id>/probe         Diagnose: GET each likely map URL (read-only), report status / code / keys
POST /api/controllers/<id>/map/csv       multipart file= (columns index,x,y,z)
DELETE /api/controllers/<id>/map
GET  /api/controllers/<id>/map.csv
GET  /api/map                            points per controller
GET  /api/frames                         last frame per controller, base64
POST /api/controllers/<id>/test          {"mode": "pattern"|"pixel"|"solid"|"off"|"stop", "pattern": "chase",
                                          "index": 10, "rgb": [255,0,0], "speed": 25, "seconds": 3}
POST /api/test                           same body plus "ids": [...] (omit = every enabled controller)
GET  /api/patterns                       pattern names and what each checks
GET  /map                                phone page for camera mapping (open over https://)
GET  /api/mapping/info                   {"https_port", "host", "session"}
POST /api/mapping/start                  {"ids": [...], "level": 128} -> session, steps, controllers and offsets
POST /api/mapping/step                   {"session", "step", "level"}: returns once that frame is out on every controller
POST /api/mapping/save                   {"session", "points": [[x,y]|null...], "colours": ["RGB"|...], "save": true}
                                          -> per controller found / filled and a per-port report
POST /api/mapping/view                   {"session", "angle", "points", "colours", "heading", "pitch", "moved"}: keep as a view
GET  /api/mapping/views                  the kept views; DELETE discards them
POST /api/mapping/combine                {"mode": "auto"|"flat"|"around", "mirror": false, "save": false}
POST /api/mapping/captures/<name>/<file> one photo (image/png) or the notes (capture.json) of a capture
GET  /api/mapping/captures               the captures sent from the phone; <name>.zip downloads one; DELETE removes it
POST /api/mapping/stop
GET  /api/export/eos_pixelmap.csv        ?pixelmap=1&flatten=xy|xz|zy|unwrap|cone|sphere&arrange=shared|tiled|separate
                                          &width=0&mode=address|channel&channel_base=1&manufacturer=Generic&fixture_type=RGB
GET  /api/export/eos_patch.csv           same parameters
GET  /api/export/eos_preview.svg         same parameters
```

---

## Development

```bash
python3 -m venv venv
```

```bash
venv/bin/pip install -r requirements-dev.txt
```

```bash
venv/bin/python -m pytest -q
```

(Windows: `venv\Scripts\python -m pip install -r requirements-dev.txt`, then
`venv\Scripts\python -m pytest -q`.)

`tests/mock_controller.py` is a fake Twinkly Pro assembled from packet captures of a real
TWPROCTRLPLC21 (fw 4.1.0): v2 REST endpoints and a real-time receiver that validates every
datagram. The tests run the whole bridge against it, send sACN and DDP, and check the bytes
that reach the "controller". To try the UI against the mock by hand (it listens on HTTP 8080
by default; add controller `127.0.0.1:8080`):

```bash
python tests/mock_controller.py
```

```bash
python tests/senders.py
```

The second sends 3 s of sACN: LED 0 red, 501 green, 1249 blue.

Protocol notes from the captures are in [docs/protocol.md](docs/protocol.md). Conventions
for changes (versioning, changelog) are in [CLAUDE.md](CLAUDE.md).

### Project structure

```
TwinklyBridge/
├── bridge/
│   ├── __init__.py          # __version__
│   ├── app.py               # Flask app, REST API, create_app() (start-up), run.py's main()
│   ├── state.py             # config file, controller registry, lock
│   ├── twinkly.py           # one controller: login, rt mode, frames, map, parameter count
│   ├── sacn.py / ddp.py     # input receivers
│   ├── engine.py            # output loop + control (HTTP) thread
│   ├── layout.py            # maps -> pixel grid, auto patch, parameter_summary()
│   ├── exports.py           # Eos pixel map / patch sheet CSV, preview SVG
│   ├── patterns.py          # test patterns
│   ├── mapping.py           # camera mapping: Gray-coded frames, gap filling, per-port report
│   ├── tls.py               # HTTPS listener for the phone page
│   ├── security.py          # allowlist, password, cross-site blocking, headers
│   ├── logs.py              # console / local syslog / remote syslog
│   ├── static/              # css/style.css, js/app.js, js/mapping.js (phone), js/mapdecode.js
│   └── templates/           # index.html (desk), map.html (phone)
├── docs/protocol.md
├── tools/                   # standalone diagnostic scripts, make_cert.py (HTTPS certificate)
├── tests/                   # pytest + mock controller
├── run.py                   # start with Flask's server (Windows, macOS, by hand)
├── wsgi.py                  # gunicorn entry point (Linux service)
├── install.sh               # Linux: venv + hardened systemd service
├── twinkly-bridge.service   # reference unit (install.sh writes the real one)
├── install.bat / run.bat    # Windows
├── requirements.txt         # pinned
├── .env.example
└── CHANGELOG.md
```

---

## Status and roadmap

Working against the mock and a single real TWPROCTRLPLC21. Not yet verified on hardware:
multi-controller groups (ordering by `group.offset`), RGBW controllers, consumer (v1)
firmware, the unwrap orientation on a real 3D-mapped tree.

- [ ] Group-aware map pull (one click for every controller in a group)
- [ ] Movie upload, so cues can trigger stored content via the Pro's Art-Net profile
- [ ] sACN sync packets
- [x] Linux service (`install.sh`)
- [ ] Windows service wrapper (today: `run.bat` or a logon scheduled task)

## Credits

The real-time datagram format and the consumer REST API were first documented by the
[xled](https://github.com/scrool/xled) project; the approach of importing a controller's
camera map as a model came from xLights' Twinkly support. Eos CSV format from ETC's
[Pixel Map Editing and Importing Through CSV](https://support.etcconnect.com/ETC/Consoles/Eos_Family/Software_and_Programming/Pixel_Map_Editing_and_Importing_Through_CSV).

## License

MIT. Twinkly and Twinkly Pro are trademarks of their owners; Eos is a trademark of ETC. This
project is not affiliated with either and relies on undocumented behaviour that may change.
