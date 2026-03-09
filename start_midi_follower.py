import time

from apcseq import Sequencer
from midi_follower import MidiFollower

# Create a MIDI clock follower (follows external MIDI clock)
clock = MidiFollower()

# Create sequencer with 4 steps per beat
seq = Sequencer(clock, steps_per_beat=4, light_steps=True)

# Start following MIDI clock
seq.play()

while True:
    time.sleep(1)
