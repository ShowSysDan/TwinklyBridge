# Twinkly Pro local protocols, as observed

Everything below was confirmed from packet captures of the iOS Twinkly app (build 1036)
talking to a **TWPROCTRLPLC21** on firmware **4.1.0**, API version 2.11.0. Consumer-firmware
behaviour is noted where it differs, from the community documentation at
https://xled-docs.readthedocs.io (the xled project), which first described the real-time
datagram format; the xLights and xled_plus sources were read for the consumer-side details.

None of this is published by Twinkly. A firmware update can change any of it.

## Discovery (UDP 5555)

Request: broadcast `01 "discover"` (9 bytes) to port 5555.

Pro reply (19 bytes for this device):

```
0a c9 73 cc  54 50  54 77 50 52 4f 5f 61 30 36 33 30 39  00
IP (normal order) "TP"  "TwPRO_a06309"                      NUL
```

Consumer firmware sends the IP **reversed** and the marker **"OK"**; the Pro sends the IP
in normal order and **"TP"**. Community tools written for consumer Twinkly only accept the
"OK" form and therefore never see a Pro.

Discovery is a broadcast and will not cross a VLAN. The bridge never relies on it.

## HTTP API (TCP 80)

Pro firmware serves the REST API under **`/xled/v2/`** (consumer firmware: `/xled/v1/`).
Replies wrap the status as `{"result": {"code": 1000}}` (consumer: top-level `"code"`).

Unauthenticated:

| Call | Notes |
|---|---|
| `GET /xled/info` | product, MAC, API version and the **protocol list** (see below) |
| `GET /xled/v2/gestalt` | `number_of_led`, `device_config.led_profile`, `device_config.ports[]` (per-port string lengths), `group`, `osc_en`, `artnet_en`, `rest_locked`, network |
| `POST /xled/v2/login` | body `{"challenge": base64(32 random bytes)}` → `authentication_token` (base64 of 8 bytes), `challenge-response` |
| `POST /xled/v2/verify` | header `X-Auth-Token`, body `{"challenge-response": …}` |

With `X-Auth-Token`:

| Call | Notes |
|---|---|
| `GET/POST /xled/v2/application/mode` | `{"mode":"rt"}` to stream; `{"mode":"movie","unique_id":…}`, `effect`, `playlist`, `off` otherwise. (Consumer: `/xled/v1/led/mode`) |
| `POST /xled/v2/led/rt/frame` | `Content-Type: application/octet-stream`, body = one full frame. The app sends an all-black frame this way when re-entering rt mode |
| `GET /xled/v2/led/layout/full` | the camera map: `source` (`2d`, `3d`, `linear`), `synthesized`, `coordinates[{x,y,z}]` in chain order |
| `GET /xled/v2/movies`, `/movies/current`, `POST /movies/current` | stored movies: `descriptor_type rgb_raw`, `leds_per_frame`, `frames_number`, `fps` |
| `GET /xled/v2/led/out/brightness` | `{"mode":"enabled","value":100}` |
| `GET /xled/v2/fw/ct1/version` | firmware (`ct0` returns 404 on this unit) |

`/xled/info` protocol list on fw 4.1.0:

```
discovery  v1 (min 1)
streaming  v4 (min 3)      <- the real-time UDP protocol; v3 datagrams still accepted
osc        v1.4 (min 1.1)
artnet     v4 (min 4)
```

Tokens are reused by the app across sessions; neither capture contained a login, so the
bridge tries `/xled/v2/login` then `/xled/v1/login`.

## Real-time streaming (UDP 7777)

Switch to `rt` mode over HTTP, then send datagrams. The app on fw 4.1.0 sends exactly the
community-documented **v3** format:

```
03 | 8 raw token bytes (base64-decoded X-Auth-Token) | 00 00 | fragment index | up to 900 bytes
```

A frame is one pixel after another in chain order, `R G B` per LED (`W R G B` for RGBW
profiles). 1250 RGB LEDs = 3750 bytes = fragments 0–3 of 900 bytes and fragment 4 of 150.
The app paced fragments ~6 ms apart and frames at ~6 fps; 20–30 fps with 2 ms between
fragments worked on the real controller. The controller stays in rt mode while frames keep
arriving and drops back to its previous mode a few seconds after they stop.

The index in the frame is the LED's only address: **controller → port → position on the
string**. Ports are concatenated in `port_id` order (gestalt `device_config.ports`), so with
five ports of 250, port *p* LED *n* (1-based) is index `p*250 + n − 1`. Camera mapping
attaches coordinates to indices and never reorders them.

## When `layout/full` is 2404 (2026-11-05)

The same TWPROCTRLPLC21 (fw 4.1.0, API 2.11.0, no group) later answered
`{"result":{"code":2404}}` on `GET /xled/v2/led/layout/full` while the Twinkly app still
showed a map for it. The bridge's Diagnose tried, logged in, under both `/xled/v2/` and
`/xled/v1/`: `led/layout/full`, `led/layout`, `led/layout/2d`, `led/layout/3d`,
`layout/full`, `layout`, `led/map`, `led/mapping`, `mapping`, `map`, `led/config`: all
2404. v2 `summary` (`app_mode`, `filters`, `group`, `led_profile`, `mac`,
`number_of_led`), `gestalt`, `led/out/brightness`, `movies`, `movies/current` answered 200
with no coordinates; v2 `playlist` GET is 2405 (method not allowed); every v1 URL is 2404
on this firmware. So the map the app shows is not always held by the controller:
the app (or the Twinkly cloud) keeps its own copy. Re-saving the mapping from the app may
write it back; unconfirmed.

## Groups (several controllers)

Each controller holds only its own chunk of the layout; `layout/full` on each, concatenated in
group order, is the whole map. The gestalt `group` object
(`mode` master/slave, `uid`, `size`, `offset`) gives each controller's starting index within
the group. Streaming to a group means one rt stream per controller, each with its own token,
sliced from the group frame by LED count; mode `rt` set on the master propagates. A master
also broadcasts short keep-alive datagrams UDP 7777→7777 every 5 s.

Group types (Twinkly help): **Join** maps all devices as one unit in a shared coordinate
space; **Sync** keeps each device's own layout; **Area** keeps each device's own layout and
places devices with the Area Editor (whether the per-device layout reflects that placement is
unverified).

## Art-Net / DMX (documented, UDP 6454)

The Pro's Art-Net profile is a 10-channel fixture, not a pixel map: 1 brightness, 2 mode
(colour / movie / playlist), 3–6 R G B W, 7 movie number (32 in steps of 8), 8 playlist,
10 on/off control. Useful for triggering stored content from a console; useless for
per-pixel output. Per-pixel over Art-Net has to go through a bridge like this one.

## OSC (documented, UDP 9000)

Enabled per device in the app ("Integration Interface Protocol"). Address
`/v1/TwPRO<mac-without-colons>`, e.g. `/v1/TwPRO304950a06309`; `/v1/TwPRO*` addresses all.
