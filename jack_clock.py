import jack
import threading
import time


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
    """Clock that follows JACK transport in client mode.

    Drop-in replacement for sc3 TempoClock. The sequencer follows JACK
    transport state (rolling/stopped) and tempo (from BBT). When no
    timebase master provides BBT, the internally set tempo is used.
    """

    routine_class = JackRoutine

    def __init__(self, client_name="apcseq"):
        self.client = jack.Client(client_name)
        self._tempo_bps = 2.0  # 120 BPM in beats per second
        self._routine = None
        self._thread = None
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
        self._thread = threading.Thread(target=self._transport_loop, daemon=True)
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

    def _transport_loop(self):
        routine = self._routine
        sequencer = routine.sequencer
        gen = routine.func((routine, self))

        was_rolling = False
        last_abs_step = None

        while not routine._stopped:
            state, pos = self.client.transport_query()

            if state != jack.ROLLING:
                was_rolling = False
                time.sleep(0.005)
                continue

            # Update tempo from JACK BBT if a timebase master provides it
            bpm = pos.get("beats_per_minute", 0)
            if bpm > 0:
                self._tempo_bps = bpm / 60

            # Derive current step from JACK beat position
            abs_beat = self._beat_position(pos)
            abs_step = int(abs_beat * sequencer.steps_per_beat)
            seq_step = abs_step % sequencer.total_steps

            if not was_rolling:
                # Transport just started — sync position and fire first step
                was_rolling = True
                sequencer.current_step = seq_step
                last_abs_step = abs_step
                try:
                    next(gen)
                except StopIteration:
                    break
                continue

            if abs_step != last_abs_step:
                # Crossed a step boundary — sync position and fire
                sequencer.current_step = seq_step
                last_abs_step = abs_step
                try:
                    next(gen)
                except StopIteration:
                    break
            else:
                time.sleep(0.001)
