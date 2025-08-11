import atexit
import time

import rtmidi
from pressed.controllers import APCMini
from pressed.pressed import Button, Knob
from sc3.base.all import Routine


class Sequencer:
    @staticmethod
    def tick(context):
        routine, clock = context
        sequencer = routine.sequencer

        while True:
            # Get current step's absolute and page-relative position
            page_step = sequencer.current_step % 8
            abs_step = sequencer.current_step

            if sequencer.light_steps:
                # Turn off previous column if needed
                if sequencer.prev_step is not None:
                    prev_page_step = sequencer.prev_step % 8
                    prev_abs_step = sequencer.prev_step
                    if prev_abs_step // 8 == sequencer.current_page:
                        prev_column = sequencer.apc.grid_columns[prev_page_step]
                        prev_sequence_column = sequencer.sequence[prev_abs_step]
                        for i, button in enumerate(prev_column):
                            if prev_sequence_column[i]:
                                sequencer.apc.light(button, "orange")
                                if not sequencer.muted_rows[i]:
                                    note = sequencer.base_note + (7 - i)
                                    sequencer.midi_out.send_message([0x80, note, 0])
                            else:
                                sequencer.apc.light(button, "off")

            column = sequencer.apc.grid_columns[page_step]
            sequence_column = sequencer.sequence[abs_step]

            for i, button in enumerate(column):
                if (
                    sequence_column[i] and not sequencer.muted_rows[i]
                ):  # Active note and unmuted
                    note = sequencer.base_note + (7 - i)
                    sequencer.midi_out.send_message([0x90, note, 100])
                # Light the column only if it's in the current page
                if sequencer.light_steps:
                    if abs_step // 8 == sequencer.current_page:
                        if sequence_column[i]:  # Active note
                            sequencer.apc.light(button, "red")
                        else:
                            sequencer.apc.light(button, "green")

            sequencer.prev_step = sequencer.current_step
            sequencer.current_step = (
                sequencer.current_step + 1
            ) % sequencer.total_steps
            yield 1 / sequencer.steps_per_beat

    def __init__(
        self, clock, base_note=36, steps_per_beat=2, total_pages=2, light_steps=True
    ):
        self.apc = APCMini()
        self.clock = clock
        self.base_note = base_note
        self.steps_per_beat = steps_per_beat
        self.total_pages = total_pages
        self.light_steps = light_steps

        self.total_steps = total_pages * 8
        self.current_page = 0

        self.midi_out = rtmidi.MidiOut()
        self.midi_out.open_virtual_port("sequencer")

        self.current_step = 0
        self.prev_step = None
        self.is_playing = False

        # Initialize sequence state - total_steps x 8 grid of booleans
        self.sequence = [[False for _ in range(8)] for _ in range(self.total_steps)]

        # Initialize mute states - all unmuted to start
        self.muted_rows = [False for _ in range(8)]

        self.apc.callbacks.append(self.handle_input)

        # With my setup where the APC Mini is connected to a powered docking
        # station, the lights will stay on even when the laptop is standby
        atexit.register(self.lights_out)

        # We need to wait a bit for the MIDI to get wired up
        time.sleep(0.25)

        # Initialize all grid buttons to off
        for button in self.apc.grid:
            self.apc.light(button, "off")

        # Light up right column (mute buttons)
        for button in self.apc.right_column:
            button.light("on")

    def handle_input(self, control, value):
        if isinstance(control, Button):
            if control in self.apc.right_column and value:
                # Handle mute buttons
                row_idx = self.apc.right_column.index(control)
                self.muted_rows[row_idx] = not self.muted_rows[row_idx]
                self.apc.light(control, "off" if self.muted_rows[row_idx] else "green")
            elif control in self.apc.grid and value:
                # Find button coordinates
                for col_idx, col in enumerate(self.apc.grid_columns):
                    if control in col:
                        row_idx = col.index(control)
                        abs_col = self.current_page * 8 + col_idx
                        # Toggle sequence state
                        self.sequence[abs_col][row_idx] = not self.sequence[abs_col][
                            row_idx
                        ]
                        # Update button light
                        self.apc.light(
                            control,
                            "orange" if self.sequence[abs_col][row_idx] else "off",
                        )
                        break
            elif control.number >= 68 and control.number <= 71:
                self.select_page(control.number - 68)
        elif isinstance(control, Knob):
            # Pass through the sliders. This isn't so efficient, but
            # it's an easy way to prevent the note messages for the grid
            # from leaking through
            self.midi_out.send_message([0xB0, control.number, value])

    def change_page(self, delta):
        new_page = min(self.total_pages - 1, max(0, self.current_page + delta))
        self.select_page(new_page)

    def select_page(self, page):
        if page != self.current_page:
            # Turn off old page indicator
            for i in range(4, 8):
                self.apc.light(self.apc.bottom_row[i], "off")

            # Show new page
            self.current_page = page
            self.refresh_grid()

            # Light up new page indicator
            self.apc.light(self.apc.bottom_row[4 + page], "green")

    def refresh_grid(self):
        base_idx = self.current_page * 8
        for col_idx, column in enumerate(self.apc.grid_columns):
            sequence_column = self.sequence[base_idx + col_idx]
            for row_idx, button in enumerate(column):
                self.apc.light(button, "orange" if sequence_column[row_idx] else "off")

    def lights_out(self):
        for button in self.apc.buttons.grid:
            button.light("off")
        for button in self.apc.buttons.right_column:
            button.light("off")
        for button in self.apc.buttons.bottom_row:
            button.light("off")

    def play(self):
        if not self.is_playing:
            self.routine = Routine(self.tick)
            self.routine.sequencer = self
            self.clock.play(self.routine)
            self.is_playing = True

    def stop(self):
        if self.is_playing:
            self.routine.stop()
            self.is_playing = False

            # Turn off all lights
            for col_idx, column in enumerate(self.apc.grid_columns):
                abs_col = self.current_page * 8 + col_idx
                for row_idx, button in enumerate(column):
                    if button.lit in ["green", "red"]:
                        if button.lit == "red":
                            note = self.base_note + (7 - row_idx)
                            self.midi_out.send_message([0x80, note, 0])
                        self.apc.light(
                            button,
                            "orange" if self.sequence[abs_col][row_idx] else "off",
                        )


if __name__ == "__main__":
    from sc3.all import *

    # Create a clock at 120 BPM
    clock = TempoClock(120 / 60)

    # Create sequencer with 4 steps per beat
    seq = Sequencer(clock, steps_per_beat=4)

    # Start the sequencer
    seq.play()

    # while True:
    #     time.sleep(0.1)

    # Stop the sequencer
    # seq.stop()
