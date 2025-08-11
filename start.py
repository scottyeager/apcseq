from sc3.all import *

from apcseq import Sequencer
from midi_clock import MidiClock

# Create a clock at 120 BPM
clock = TempoClock(120 / 60)

# Create sequencer with 4 steps per beat
seq = Sequencer(clock, steps_per_beat=4, light_steps=True)


def set_tempo(slider):
    clock.tempo = (slider.value * 240) / 60


seq.apc.sliders[8].value_change_action = set_tempo

# Start the sequencer
seq.play()

# Also set up midi sync
midi_sync = MidiClock(clock)
midi_sync.play()
