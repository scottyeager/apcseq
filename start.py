from sc3.all import *
from midi_clock import MidiClock
from apcseq import Sequencer

# Create a clock at 120 BPM
clock = TempoClock(120 / 60)

# Create sequencer with 4 steps per beat
seq = Sequencer(clock, steps_per_beat=4, light_steps=False)

# Start the sequencer
seq.play()

# Also set up midi sync
midi_sync = MidiClock(clock)
midi_sync.play()
