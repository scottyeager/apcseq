from jack_clock import JackClock
from apcseq import Sequencer

# Create a JACK transport clock (client mode - follows JACK transport)
clock = JackClock()

# Create sequencer with 4 steps per beat
seq = Sequencer(clock, steps_per_beat=4, light_steps=True)

# Start following JACK transport
seq.play()
