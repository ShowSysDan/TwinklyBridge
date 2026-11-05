"""Two threads: the output loop builds frames and sends UDP at a fixed rate; the control
thread does all HTTP (login, rt mode, restore) so a slow controller can't stall output."""
import threading
import time

from .state import CONFIG, CONTROLLERS, lock
from .twinkly import RT_PORT


class OutputEngine(threading.Thread):
    def __init__(self, sacn, ddp):
        super().__init__(daemon=True, name="output")
        self.sacn, self.ddp = sacn, ddp
        self.fps_actual = 0.0
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        sent, tick = 0, time.perf_counter()
        next_t = time.perf_counter()
        while not self._stop.is_set():
            period = 1.0 / max(1, CONFIG["output"]["fps"])
            idle = CONFIG["sacn"]["idle_timeout_s"]
            cpu = CONFIG["sacn"]["channels_per_universe"]
            now = time.time()
            with lock:
                ctrls = list(CONTROLLERS.values())
            queue = []
            for c in ctrls:
                try:
                    frame = self.frame_for(c, now, idle, cpu)
                    if frame is not None:
                        queue.append((c, c.fragments(frame), frame))
                except Exception as e:                       # noqa: BLE001
                    c.status, c.error = "error", f"{type(e).__name__}: {e}"
            self.send_interleaved(queue)
            sent += 1
            t = time.perf_counter()
            if t - tick >= 1.0:
                self.fps_actual = sent / (t - tick)
                sent, tick = 0, t
            next_t += period
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.perf_counter()

    def frame_for(self, c, now, idle, cpu):
        """What this controller should show this tick; None = nothing to send."""
        if not c.enabled or not c.n_led:
            c.want_live = False
            return None
        if c.testing(now):
            c.want_live = True
            return c.test["frame"] if c.rt_ready(now) else None
        if c.source == "ddp":
            live = self.ddp.live(c.ddp_start - 1, c.n_led * c.bpp, idle)
        else:
            live = any(now - self.sacn.last_seen.get(u, 0) < idle for u in c.universes(cpu))
        c.want_live = live
        if live and c.rt_ready(now):
            return c.build_frame_ddp(self.ddp.buffer) if c.source == "ddp" \
                else c.build_frame_sacn(self.sacn.universes, cpu)
        return None

    def send_interleaved(self, queue):
        """Fragment 0 of every controller, pause, fragment 1 of every controller, ... so the
        inter-fragment pause does not multiply by the number of controllers."""
        gap = CONFIG["output"]["fragment_gap_ms"] / 1000.0
        rounds = max((len(f) for _, f, _ in queue), default=0)
        for k in range(rounds):
            for c, frags, _ in queue:
                if k < len(frags):
                    try:
                        c.sock.sendto(frags[k], (c.host, RT_PORT))
                    except OSError as e:
                        c.status, c.error = "error", f"udp: {e}"
            if gap and k < rounds - 1:
                time.sleep(gap)
        for c, _, frame in queue:
            c.last_frame = frame
            c.frames_sent += 1
            c.active = True


class ControlThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="control")
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            now = time.time()
            with lock:
                ctrls = list(CONTROLLERS.values())
            for c in ctrls:
                try:
                    if c.test and now >= c.test["until"]:
                        c.test = None
                    if c.want_live or c.testing(now):
                        c.ensure_rt()
                    elif c.active:
                        c.active = False
                        c.restore_mode()
                except Exception as e:                       # noqa: BLE001
                    c.status, c.error = "error", f"{type(e).__name__}: {e}"
            self._stop.wait(0.25)
