import jack


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


class JackClock:
    """Clock that follows JACK transport using the process callback.

    Inspired by Hydrogen's approach: uses JACK's process callback for
    sample-accurate step timing instead of polling with time.sleep().

    The process callback is invoked by JACK every buffer period (e.g.
    every 1024 frames at 48kHz ≈ 21ms). On each call we query transport
    state, compute the current step from BBT, and fire the sequencer
    when a step boundary is crossed — including any steps that were
    skipped between callbacks.
    """

    routine_class = JackRoutine

    def __init__(self, client_name="apcseq"):
        self.client = jack.Client(client_name)
        self._tempo_bps = 2.0  # 120 BPM in beats per second
        self._routine = None
        self._gen = None
        self._steps_per_beat = 4
        self._total_steps = 16
        self._last_abs_step = None

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

        sequencer = routine.sequencer
        self._steps_per_beat = sequencer.steps_per_beat
        self._total_steps = sequencer.total_steps

        self._gen = routine.func((routine, self))
        self._last_abs_step = None

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
            frame_rate = pos["frame_rate"]
            return frame / frame_rate * self._tempo_bps

    def _fire_step(self, seq_step):
        """Set the sequencer position and advance the generator."""
        self._routine.sequencer.current_step = seq_step
        try:
            next(self._gen)
        except StopIteration:
            self._routine._stopped = True

    def _process(self, frames):
        """JACK process callback — called every buffer period.

        Queries transport, detects step boundaries, and fires the
        sequencer. Handles skipped steps (large buffer sizes) and
        transport relocation (rewind/jump).
        """
        routine = self._routine
        if routine is None or routine._stopped:
            return

        state, pos = self.client.transport_query()

        if state != jack.ROLLING:
            self._last_abs_step = None
            return

        # Sync tempo from timebase master
        bpm = pos.get("beats_per_minute", 0)
        if bpm > 0:
            self._tempo_bps = bpm / 60

        abs_beat = self._beat_position(pos)
        abs_step = int(abs_beat * self._steps_per_beat)
        last = self._last_abs_step

        if last is not None and abs_step == last:
            return  # Still on the same step

        # Transport relocated backwards — just jump to the new position
        if last is not None and abs_step < last:
            self._last_abs_step = abs_step
            self._fire_step(abs_step % self._total_steps)
            return

        # Fire any steps we skipped over (e.g. large buffer size)
        if last is not None:
            for missed in range(last + 1, abs_step):
                self._fire_step(missed % self._total_steps)

        self._last_abs_step = abs_step
        self._fire_step(abs_step % self._total_steps)
