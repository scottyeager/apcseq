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

    def _transport_loop(self):
        routine = self._routine
        sequencer = routine.sequencer
        gen = routine.func((routine, self))

        was_rolling = False

        while not routine._stopped:
            state, pos = self.client.transport_query()

            if state != jack.ROLLING:
                was_rolling = False
                time.sleep(0.005)
                continue

            # Sync position on transport start from frame 0
            if not was_rolling:
                was_rolling = True
                if pos.get("frame", -1) == 0:
                    sequencer.current_step = 0

            # Update tempo from JACK BBT if a timebase master provides it
            bpm = pos.get("beats_per_minute", 0)
            if bpm > 0:
                self._tempo_bps = bpm / 60

            # Execute one step of the sequencer
            try:
                step_beats = next(gen)
            except StopIteration:
                break

            # Wait for the step duration according to current tempo
            if self._tempo_bps > 0:
                step_seconds = step_beats / self._tempo_bps
            else:
                step_seconds = 0.125

            deadline = time.monotonic() + step_seconds
            while time.monotonic() < deadline and not routine._stopped:
                time.sleep(0.001)

                state, pos = self.client.transport_query()
                if state != jack.ROLLING:
                    was_rolling = False
                    # Transport stopped mid-step, wait for it to resume
                    while not routine._stopped:
                        state, _ = self.client.transport_query()
                        if state == jack.ROLLING:
                            was_rolling = True
                            break
                        time.sleep(0.005)
                    break

                # Track tempo changes while waiting
                bpm = pos.get("beats_per_minute", 0)
                if bpm > 0:
                    self._tempo_bps = bpm / 60
