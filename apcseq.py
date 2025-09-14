import atexit
import time

import rtmidi
from sc3.base.all import Routine

from pressed.controllers import APCMini
from pressed.pressed import Button, Knob


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
                    prev_abs_step = sequencer.prev_step
                    if prev_abs_step // 8 == sequencer.current_page:
                        prev_page_step = prev_abs_step % 8
                        prev_column = sequencer.apc.grid_columns[prev_page_step]
                        prev_page_buttons = sequencer.apc.button_sets[
                            prev_abs_step // 8
                        ]
                        prev_grid_col_buttons = prev_page_buttons.grid_columns[
                            prev_page_step
                        ]

                        for i, button in enumerate(prev_column):
                            is_on = getattr(prev_grid_col_buttons[i], "is_on", False)
                            button.light("orange" if is_on else "off")
                            if is_on and not sequencer.muted_rows[i]:
                                note = sequencer.base_note + (7 - i)
                                sequencer.midi_out.send_message([0x80, note, 0])

            # Light up current column
            column = sequencer.apc.grid_columns[page_step]
            page_idx = abs_step // 8
            page_buttons = sequencer.apc.button_sets[page_idx]
            grid_col_buttons = page_buttons.grid_columns[page_step]

            for i, button in enumerate(column):
                is_on = getattr(grid_col_buttons[i], "is_on", False)
                if is_on and not sequencer.muted_rows[i]:  # Active note and unmuted
                    note = sequencer.base_note + (7 - i)
                    sequencer.midi_out.send_message([0x90, note, 100])

                # Light the column only if it's in the current page
                if sequencer.light_steps:
                    if abs_step // 8 == sequencer.current_page:
                        if is_on:  # Active note
                            button.light("red")
                        else:
                            button.light("green")

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

        # Create a button set for each page
        for _ in range(self.total_pages - 1):
            self.apc.add_button_set()

        # Add is_on attribute to all grid buttons for sequence state
        for page_buttons in self.apc.button_sets:
            for button in page_buttons.grid:
                button.is_on = False

        self.midi_out = rtmidi.MidiOut()
        self.midi_out.open_virtual_port("sequencer")

        self.current_step = 0
        self.prev_step = None
        self.is_playing = False

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
            button.light("off")

        # Light up right column (mute buttons) and set lit state on all pages
        for page_buttons in self.apc.button_sets:
            for button in page_buttons.right_column:
                button.lit = "on"
        for button in self.apc.right_column:
            button.light("on")

        # Set initial page indicator
        self.select_page(0)

    def handle_input(self, control, value):
        if isinstance(control, Button):
            if control in self.apc.right_column and value:
                # Handle mute buttons
                row_idx = self.apc.right_column.index(control)
                self.muted_rows[row_idx] = not self.muted_rows[row_idx]
                new_state = "off" if self.muted_rows[row_idx] else "green"

                # Update lit state on all pages
                for page_buttons in self.apc.button_sets:
                    page_buttons.right_column[row_idx].lit = new_state
                # Light the button on the active page
                control.light(new_state)

            elif control in self.apc.grid and value:
                # Toggle sequence state
                control.is_on = not getattr(control, "is_on", False)
                control.light("orange" if control.is_on else "off")

            elif control.number >= 68 and control.number <= 71 and value:
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
            old_page_index = self.current_page

            # Clean up playhead from the old page if it's currently there
            if self.is_playing and self.prev_step is not None:
                prev_page_of_playhead = self.prev_step // 8
                if prev_page_of_playhead == old_page_index:
                    col_index_on_old_page = self.prev_step % 8
                    column_to_clear = self.apc.grid_columns[col_index_on_old_page]
                    button_states_from_model = self.apc.button_sets[
                        old_page_index
                    ].grid_columns[col_index_on_old_page]

                    for i, button in enumerate(column_to_clear):
                        is_on = getattr(button_states_from_model[i], "is_on", False)
                        button.light("orange" if is_on else "off")

            # Update lit state for all page indicators on all pages
            for page_buttons in self.apc.button_sets:
                page_buttons.bottom_row[4 + self.current_page].lit = "off"
                page_buttons.bottom_row[4 + page].lit = "green"

            self.current_page = page
            self.apc.activate_button_set(self.apc.button_sets[page])
        # Handle initial page selection
        elif not hasattr(self, "is_playing"):
            for page_buttons in self.apc.button_sets:
                for i in range(4, 8):
                    page_buttons.bottom_row[i].lit = "off"
                page_buttons.bottom_row[4 + page].lit = "green"
            self.apc.activate_button_set(self.apc.button_sets[page])

    def lights_out(self):
        for button in self.apc.buttons:
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

            # Turn off all lights that were part of the playhead
            for column in self.apc.grid_columns:
                for button in column:
                    if button.lit in ["green", "red"]:
                        if button.lit == "red":
                            # This is a bit of a hack to find the note to turn off
                            # A better way would be to store which notes are on
                            row_idx = -1
                            for i, r in enumerate(self.apc.grid_columns):
                                if button in r:
                                    row_idx = r.index(button)
                                    break
                            if row_idx != -1:
                                note = self.base_note + (7 - row_idx)
                                self.midi_out.send_message([0x80, note, 0])

                        is_on = getattr(button, "is_on", False)
                        button.light("orange" if is_on else "off")


if __name__ == "__main__":
    from sc3.all import *

    # Create a clock at 120 BPM
    clock = TempoClock(120 / 60)

    # Create sequencer with 4 steps per beat
    seq = Sequencer(clock, steps_per_beat=4, total_pages=4)

    # Start the sequencer
    seq.play()

    # while True:
    #     time.sleep(0.1)

    # Stop the sequencer
    # seq.stop()