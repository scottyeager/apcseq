import jack
import threading


class JackRoutine:
    """Simple routine wrapper for use with JackClock.

    Provides the same interface as sc3's Routine needed by the Sequencer:
    - Accepts a generator function
    - Allows setting arbitrary attributes (e.g. .sequencer)
    - Has a .stop() method
    """

    def __init__(self, func):
        self.func = func
        self._stopped = False

    def stop(self):
        self._stopped = True
        # Wake the worker thread so it can exit
        if hasattr(self, "_step_event"):
            self._step_event.set()


class JackClock:
    """Clock that follows JACK transport in client mode.

    Drop-in replacement for sc3 TempoClock. The sequencer follows JACK
    transport state (rolling/stopped) and tempo (from BBT). When no
    timebase master provides BBT, the internally set tempo is used.

    Uses JACK's process callback to detect step boundaries, so there
    is no polling — the check runs exactly once per audio cycle.
    A worker thread wakes via Event to do the actual sequencer work
    (MIDI, LEDs) outside the realtime callback.
    """

    routine_class = JackRoutine

    def __init__(self, client_name="apcseq"):
        self.client = jack.Client(client_name)
        self._tempo_bps = 2.0  # 120 BPM in beats per second
        self._routine = None
        self._thread = None

        self._step_event = threading.Event()
        self._was_rolling = False
        self._last_abs_step = None
        self._seq_step = 0
        self._steps_per_beat = 4
        self._total_steps = 16

        self.client.set_process_callback(self._process)
        self.client.activate()

    @property
    def tempo(self):
        return self._tempo_bps

    @tempo.setter
    def tempo(self, value):
        self._tempo_bps = value

    def play(self, routine):
        """Start following JACK transport, driving the given routine."""
        self._routine = routine
        routine._step_event = self._step_event

        sequencer = routine.sequencer
        self._steps_per_beat = sequencer.steps_per_beat
        self._total_steps = sequencer.total_steps

        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _beat_position(self, pos):
        """Calculate absolute beat position from JACK transport position.

        Uses BBT (Bar/Beat/Tick) when a timebase master provides it,
        otherwise falls back to frame-based calculation using internal tempo.
        """
        if "beat" in pos and "bar" in pos:
            bar = pos["bar"]
            beat = pos["beat"]
            tick = pos.get("tick", 0)
            ticks_per_beat = pos.get("ticks_per_beat", 1920.0)
            beats_per_bar = pos.get("beats_per_bar", 4.0)
            return (bar - 1) * beats_per_bar + (beat - 1) + tick / ticks_per_beat
        else:
            frame = pos.get("frame", 0)
            frame_rate = pos.get("frame_rate", 48000)
            return frame / frame_rate * self._tempo_bps

    def _process(self, frames):
        """JACK process callback — runs once per audio cycle.

        Lightweight: just reads transport state, does arithmetic, and
        sets an Event when a step boundary is crossed.
        """
        state, pos = self.client.transport_query()

        if state != jack.ROLLING:
            self._was_rolling = False
            return

        bpm = pos.get("beats_per_minute", 0)
        if bpm > 0:
            self._tempo_bps = bpm / 60

        abs_beat = self._beat_position(pos)
        abs_step = int(abs_beat * self._steps_per_beat)

        if not self._was_rolling or abs_step != self._last_abs_step:
            self._was_rolling = True
            self._seq_step = abs_step % self._total_steps
            self._last_abs_step = abs_step
            self._step_event.set()

    def _worker(self):
        """Worker thread — wakes on step events to drive the sequencer."""
        routine = self._routine
        sequencer = routine.sequencer
        gen = routine.func((routine, self))

        while not routine._stopped:
            self._step_event.wait()
            self._step_event.clear()
            if routine._stopped:
                break
            sequencer.current_step = self._seq_step
            try:
                next(gen)
            except StopIteration:
                break
