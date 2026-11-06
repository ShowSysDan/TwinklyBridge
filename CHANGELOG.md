# Changelog

Every change the user gets is its own version: bump `bridge/__init__.py` and
`pyproject.toml` together and add a section here (`test_version_matches_everywhere`
fails if they disagree). Semver: patch = fix, minor = feature, major = breaking.

## [0.15.0] - 2026-11-06

**3D maps: the depth of a hidden LED is guessed far better, and the map now says which
positions are measurements and which are guesses.**

Measured on a new 3D ground-truth rig (three props - a two-faced flat star, a wrapped cone,
a flat star with trees in front and a frame behind - 1250 LEDs in 5 ports, rendered from
several angles with real occlusion, see-through and the decoder's own losses, against known
positions). Over 48 held-out sets with fresh geometry, fresh noise, 5% mis-decoded LEDs and
both extremes of tinsel transparency, **every one of the 48 improved**: median 3D error
11.9 -> 5.7 px at 960 wide, LEDs placed on the wrong side of the piece 4.5% -> 1.3%, worst
set 17.2% -> 6.8%.

- **An LED seen from one angle takes its depth from its own string**, interpolated between
  the nearest measured LED before it and after it on the same port (within 10 LEDs), instead
  of averaging the six nearest by LED number across port boundaries. "The string runs between
  two points we measured" invents no shape; the old rule could pull an LED onto the wrong face.
  This is where most of the gain is.
- **The gap fill no longer assumes a cylinder unless the piece is one.** Filling a hidden run
  "round the back" is right for a tree or column and wrong for a star or a sign, where every
  LED shares the vertical axis and the run gets swung out into space. The route is now chosen
  from how deep the measured LEDs reach compared with how wide they spread: near 1 for a round
  piece (fill round the axis, as before), well under half for anything with a front and a back
  (fill along a smooth curve through the LEDs that were seen). A long gap, or one whose own
  ends were guessed, goes straight, and no filled path is ever longer than the wire it stands
  for - a string cannot stretch.
- **The typed view angles are refined against the other views.** Each view's angle was being
  judged against a map that view itself helped build, where its current angle always looks
  right, so the search stalled. It is now judged against a map built without it, with the
  view's own scale and offsets re-fitted at every candidate angle, and only moved when that
  clearly cuts the error. The page warns when a view had to be moved the full 15 degrees,
  which usually means a compass read badly near metal.
- **Measured / estimated / filled is carried per LED** out of the solver, into the per-port
  report (with the longest run that has no measurement in it), the saved map, `GET /api/map`
  and `POST /api/mapping/combine`. The build page leads with how many LEDs were never
  photographed rather than how many were seen twice - the second number goes UP when you
  stand still and photograph the same side again. The preview draws estimated LEDs faded, and
  the live map draws never-seen ones as rings as before.
- With only two views the page now says plainly that "measured" does not yet mean front and
  back are told apart: two views from nearly the same side agree with each other and are still
  both looking at the front.

Redeploy: Linux `git pull && sudo bash install.sh`; Windows `git pull`, `install.bat`, restart
`run.bat`. Existing saved maps keep working; re-run a capture to get the new per-LED detail.

## [0.14.0] - 2026-11-06

**The Eos export no longer folds a flat piece's back face onto its front.**

- `unwrap` does not mean "flatten": it unrolls a cylinder around the VERTICAL AXIS. That is
  right for the trees and columns it was written for, and wrong for a flat piece standing
  upright - a star's LEDs all sit at much the same angle round that axis, so the star's shape
  collapses into two strips at the edges of the map. The export defaulted every 3D map to it.
- New flatten mode **`faces`**: lay a flat piece out in its OWN plane, so its shape survives.
  The plane is fitted from the LEDs (so the piece can stand at any angle to the camera), and if
  the piece really has two layers, the back one is placed beside the front rather than on top
  of it, keeping left and right as they are in the room. Measured on a synthetic two-faced
  star: with the front view, all 250 opposite pairs of LEDs land exactly on top of each other;
  with `faces`, none do.
- A piece whose LEDs merely wobble in depth - tinsel on a single face - is **not** split in
  two. Splitting needs a clear empty band across the depths with at least a fifth of the LEDs
  either side, so one sheet stays one sheet.
- New flatten mode **`auto`**, and it is now what a 3D map defaults to: `faces` for a
  plate-shaped piece, `unwrap` for a round one. The test is whether the piece's horizontal
  spread is along one direction or both.

## [0.13.1] - 2026-11-06

- The camera page says what the per-pattern wait actually is now (the round trip to the bridge
  plus the camera's lag with a margin, counted from sending the pattern), and says to connect
  the phone directly rather than through a VPN - measured, every 100 ms of round trip costs
  about 3 s on a 1,250-LED capture, and it is the largest single item in one.

## [0.13.0] - 2026-11-06

- **Mapping now works hand-held in a dark room too, and lining the photos up got faster.** The
  coarse stage of the alignment ran on a quarter-size copy of the picture, so it could only
  test shifts that were multiples of 4 px. An LED is about 1.3 px across, so a drift that
  landed about 2 px off that step was invisible to it: it returned an arbitrary offset around
  9 px out, far beyond what the fine pass could rescue. It failed at drifts of 2, 6 and 10 px
  and was exact everywhere else - a mod-4 signature, which is why it looked like noise. The
  coarse search now steps a whole pixel over a thinned mask, which costs less than the
  downsampling it replaces. In the synthetic dark-room hand-held case that is 76 LEDs found
  with 54 misplaced, against **1127 found and none misplaced**, the same as that scene gives
  with the phone on a tripod. The real-capture result is unchanged (431 at 0.96) and lining up
  a 29-photo capture goes from 1.75 s to 1.55 s. The expected-failure test from 0.12.0 is now
  a passing one.
- A direct regression test for it (`tests/js/shift_probe.js`): slide one photo by every whole
  number of pixels from 0 to 12 and check each comes back. The old code gives 22.9 px of error
  at its worst and is no less confident when it is wrong; a test that tries only one drift
  cannot see a fault like this.

## [0.12.4] - 2026-11-06

- **Each photo is lined up once during a capture instead of three times.** The check for a
  photo taken before the LEDs changed lined the photo up with the all-on photo inside a
  condition that was evaluated twice, and then lined the same photo up again to keep as the
  next photo's reference. Lining one up costs about 30 ms, and there are 27 of them: measured,
  that was more time than the whole final decode. Same check, same threshold, one alignment.

## [0.12.3] - 2026-11-06

Capture time. Measured end to end, most of a capture is not photography or arithmetic: it is
waiting for the round trip to the bridge, and for a wait sized from it.

- **Only the camera's lag gets the safety margin now, not the round trip.** 0.12.0 folded the
  round trip into the measured "camera lag" so that the wait could be counted from sending a
  pattern rather than from the bridge's answer - which is right, because the LEDs change while
  the request is still in flight - but the 1.3x margin then applied to the round trip as well.
  On a 440 ms link that turned a 150 ms wait into 697 ms on every one of 33 patterns. The wait
  is now `round trip + 1.3 x camera lag + 60 ms`, counted from sending, and every wait after a
  step subtracts how long that step actually took, so a quick answer is not punished and a slow
  one is compensated.
- **Every step's round trip is recorded and reported**, not just the one sample the camera
  timing happened to take. The result line now shows the median and range. This is the biggest
  single line item in a capture and it was invisible.
- README: connect the phone to the bridge directly rather than through a VPN. Every 100 ms of
  round trip costs about 3 s on a 1,250-LED capture.

## [0.12.2] - 2026-11-06

- **The capture now shows how good it is, not just how much it found.** A bad decode can
  "find" plenty of LEDs by scattering them anywhere, and the count alone looks healthy when it
  does - the capture that started all this reported 457 of 1250 while fewer than half were
  really placed. The result page now leads with the share of LEDs that landed next to the one
  before them on the string (over 85% reads cleanly), and says plainly when the count cannot
  be trusted. The "less than half found" warning is gone: one view only ever sees the LEDs
  facing the camera, so half is normal and the warning was noise.
- **The stale-photo check no longer depends on the phone's exposure.** It compared raw
  brightness over every pixel the LEDs could reach, which in a lit room is mostly scattered
  glow, so 25 s of auto-white-balance drift could read as "the pattern changed" or hide that
  it had not. It is now compared band-passed and only at the LED dots: measured on a real
  capture, a genuine change between consecutive patterns scores 0.15 at worst and a repeat of
  the previous pattern scores 0.00, against a threshold of 0.08.

## [0.12.1] - 2026-11-06

- **A capture sent for diagnosis can now be re-read the way the phone read it.** The pattern
  photos are held as brightness plus green-ness, but only brightness was written into the
  uploaded JPEG, so the green-ness - the signal a lit-room capture is usually decoded from -
  was thrown away. Each pattern photo now also goes up as `<step>_green.jpg`. It is a second
  grey JPEG rather than one colour one because JPEG stores colour at half resolution, which
  would blur a 3 px dot. The per-capture file limit goes from 60 to 80 to fit them.

## [0.12.0] - 2026-11-06

Camera mapping: the decoder rewritten. Measured on a real 1,250-LED capture (a tinsel star in
a lit warehouse, photographed hand-held), the same photos that previously produced 457 "found"
LEDs of which only about 200 were really placed now produce **430 placed correctly**: the
measured LED pitch comes out at the piece's true 41 px instead of a meaningless 46.7, and the
share of neighbouring LEDs sitting a plausible distance apart goes from 0.37 to 0.96.

- **Every decision is made on the band-passed picture, not the raw pixel.** Tinsel scatters
  each LED's light over the whole piece, so with half the LEDs lit at once most of the
  brightness at an LED's own position is other LEDs' light: measured, an LED supplies only
  ~27% of the raw level at its own core. A top-hat (the 3 px core minus the ring around it)
  cancels that smooth glow, after which ~90% of what is left is the LED's own light.
- **LEDs are found first, then decoded.** The old "every pixel brighter than 30" mask selected
  17.5% of a lit-room frame - the dots plus the glow over all the tinsel plus every edge in
  the room - which was useless as a spot list and useless to line photos up on. Spots are now
  local maxima of the top-hat, with a self-calibrating threshold that holds in a dark room and
  in a lit warehouse.
- **Drift is measured properly and taken out with an affine fit.** The old alignment scored a
  shift by how bright the LEDs were under it, which slides every pattern onto its own lit
  subset; it reported "drift 0" on a capture that had drifted 22 px and corrected nothing.
  Photos are now matched band-passed against the all-on photo, on the room's own static
  texture where there is any, tracked photo by photo (a hand wanders ~4 px between photos, so
  each is searched around the last), and corrected with an affine fit from a 4x6 grid of block
  re-estimates - which also removes the few tenths of a degree of roll from aiming up at a
  piece. A plain translation leaves up to 7 px across the frame and loses about 45% of the LEDs.
- **The code is read as a whole word, not eleven separate bits.** Only 1,250 of the 2,048
  possible 11-bit words are real LEDs, so each spot is matched against all of them; a bit that
  came out marginal is corrected instead of throwing the LED away.
- **"The phone moved" no longer cries wolf.** It compared the all-on photos from the start and
  end of the capture raw, so 25 seconds of the phone's own auto-white-balance drift read as
  movement. It is now a gain-invariant correlation of the band-passed photos at the LEDs,
  after alignment: it reports movement the alignment could not undo, which is the only kind
  that matters.
- **The live aim view no longer hides the camera picture.** It painted every pixel that
  brightened by 25 solid green, which on a tinsel piece is 19% of the preview held still and
  up to 47% in the hand (a bright edge enters the 2-second window at one place and leaves at
  another) - the reported "the camera display just disappears". It now rings each LED it finds:
  a few hundred small markers, a few percent of the picture, with a hard ceiling on how much
  can ever be drawn. The aim canvas went from 320 to 480 px wide so 3 px dots can be found at all.
- **The wait after each pattern is measured from the right moment.** `cameraLag()` started its
  stopwatch after the HTTP round trip, but the bridge starts sending the new frame while the
  request is still in flight, so the lag came out short by however long the round trip took and
  any later pattern with a faster round trip was photographed before it appeared. The lag is
  now timed from before the request, and the wait counts from the request too, so a varying
  round trip cannot eat it.
- **The synthetic camera in the tests is now physically faithful**, which is why none of the
  above was caught earlier: it placed LEDs 2.2 px apart in a straight line with no scattered
  glow, a scene that made "find the dots first" look like it merged neighbours and made
  alignment look unnecessary. It now uses the measured geometry of a real draped string (~20 px
  along the wire, ~6 px to the nearest LED on the next fold, irregular), a glow pedestal
  calibrated to the measured ~30% own-share, camera roll, a continuous drift instead of
  independent per-frame jumps, and a room that moves with the camera. New cases cover a
  hand-held phone in a lit room and in the dark.
- Known limit, recorded as an expected failure in the tests: a blacked-out room, hand-held,
  with LEDs that show no bloom at all. There is then nothing to line the photos up on but the
  LEDs themselves, and a coding photo lights only half of them, so a shift that maps the string
  onto itself scores as well as the true one. Any room light, or any bloom, resolves it; the
  result degrades visibly rather than producing a confident wrong map.

## [0.11.2] - 2026-11-05

LEDs not lighting for test patterns or mapping:

- **The bridge takes the controller back from another app.** The Twinkly app (open on a
  phone) logs in to the controller itself, which makes the bridge's login stale - the
  controller then silently drops the bridge's frames - or switches it to another mode; the
  bridge only re-asserted rt every 30 s. While streaming it now checks the controller's mode
  every 5 s and, if another app switched it or replaced the login, logs in again and takes
  it back at once. The controller's card says when this happened ("Another app took this
  controller ..."); close the Twinkly app while the bridge is in use. Tested against the mock
  for both cases.
- **The phone page hands the LEDs back**: the live aim view stops blinking when the page is
  hidden, the phone locked or the tab closed (and tells the bridge), and by itself after 2
  minutes without a capture (*Resume the live view* restarts it); coverage colours show for 2
  minutes instead of 10. `POST /api/mapping/stop` now also clears any leftover mapping or
  coverage frames.
- **Camera watchdog on the live view**: a paused preview is restarted and a stopped camera
  (ended, or no picture for 2 s) reopened; a line under the picture shows the camera's size
  and frame rate, or what is wrong. Camera trouble is reported to the bridge's log
  (`POST /api/mapping/log`).

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
