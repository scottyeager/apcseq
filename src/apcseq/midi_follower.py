import rtmidi


class MidiFollowerRoutine:
    """Routine wrapper for MidiFollower, matching the interface Sequencer expects."""

    def __init__(self, func):
        self.func = func
        self._stopped = False

    def stop(self):
        self._stopped = True


class MidiFollower:
    """Clock that follows external MIDI clock messages.

    Listens for MIDI realtime messages:
    - 0xF8: Clock tick (24 per quarter note)
    - 0xFA: Start
    - 0xFB: Continue
    - 0xFC: Stop

    Fires sequencer steps by counting clock ticks. For steps_per_beat=4,
    a step fires every 24/4 = 6 clock ticks.
    """

    routine_class = MidiFollowerRoutine

    def __init__(self, port_name="midi_clock_in"):
        self._tempo_bps = 2.0  # 120 BPM default (not used for timing, just for display)
        self._routine = None
        self._gen = None
        self._steps_per_beat = 4
        self._total_steps = 16
        self._tick_count = 0
        self._ticks_per_step = 6  # 24 ppqn / 4 steps_per_beat
        self._running = False

        self._midi_in = rtmidi.MidiIn(name="apcseq")
        self._midi_in.open_virtual_port(port_name)
        self._midi_in.ignore_types(sysex=True, timing=False, active_sense=True)

    @property
    def tempo(self):
        return self._tempo_bps

    @tempo.setter
    def tempo(self, value):
        self._tempo_bps = value

    def play(self, routine):
        """Start following MIDI clock, driving the given routine."""
        self._routine = routine

        sequencer = routine.sequencer
        self._steps_per_beat = sequencer.steps_per_beat
        self._total_steps = sequencer.total_steps
        self._ticks_per_step = 24 // self._steps_per_beat

        self._gen = routine.func((routine, self))
        self._tick_count = 0
        self._running = False

        self._midi_in.set_callback(self._midi_callback)

    def _fire_step(self):
        """Advance the sequencer by one step."""
        if self._routine is None or self._routine._stopped:
            return
        seq_step = (self._tick_count // self._ticks_per_step) % self._total_steps
        self._routine.sequencer.current_step = seq_step
        try:
            next(self._gen)
        except StopIteration:
            self._routine._stopped = True

    def _midi_callback(self, event, data=None):
        message, delta_time = event
        status = message[0]

        if status == 0xFA:  # Start
            self._tick_count = 0
            self._running = True
            self._fire_step()
        elif status == 0xFB:  # Continue
            self._running = True
        elif status == 0xFC:  # Stop
            self._running = False
        elif status == 0xF8:  # Clock tick
            if not self._running:
                return
            self._tick_count += 1
            if self._tick_count % self._ticks_per_step == 0:
                self._fire_step()
