import atexit
import time

import rtmidi
from pressed.controllers import APCMini


class Sequencer:
    @staticmethod
    def tick(context):
        routine, clock = context
        sequencer = routine.sequencer

        while True:
            page = sequencer.current_step // 8
            page_step = sequencer.current_step % 8

            column = sequencer.page_button_sets[page].grid_columns[page_step]

            for i, button in enumerate(column):
                if (
                    button.is_on and not sequencer.muted_rows[i]
                ):  # Active note and unmuted
                    note = sequencer.base_note + (7 - i)
                    sequencer.midi_out.send_message([0x90, note, 100])

            sequencer.light_column(page, page_step, True)
            previous_column = (page_step - 1) % 8
            if previous_column == 7:
                previous_page = (page - 1) % sequencer.total_pages
            else:
                previous_page = page
            sequencer.light_column(previous_page, previous_column, False)

            sequencer.prev_step = sequencer.current_step
            sequencer.current_step = (
                sequencer.current_step + 1
            ) % sequencer.total_steps
            yield 1 / sequencer.steps_per_beat

    def __init__(
        self,
        clock,
        apc=None,
        page_button_sets=None,
        base_note=36,
        steps_per_beat=2,
        total_pages=2,
        light_steps=True,
    ):
        self.apc = apc or APCMini()
        self.clock = clock
        self.base_note = base_note
        self.steps_per_beat = steps_per_beat
        self.light_steps = light_steps

        self.tempo = self.clock.tempo * 60
        self.tempo_mode = False

        if page_button_sets:
            self.page_button_sets = page_button_sets
            self.total_pages = len(page_button_sets)
        else:
            self.total_pages = total_pages
            self.page_button_sets = [self.apc.buttons]
            for page in range(self.total_pages - 1):
                button_set = self.apc.add_button_set(
                    bottom_row=self.apc.bottom_row,
                    right_column=self.apc.right_column,
                    shift=self.apc.shift,
                )
                self.page_button_sets.append(button_set)

        self.total_steps = self.total_pages * 8
        self.current_page = 0

        for i, button_set in enumerate(self.page_button_sets):
            for button in button_set.grid:
                button.page = i
                button.is_on = False
                button.press_action = self.grid_callback

        # Use the first page set's bottom_row and right_column as the
        # canonical ones for the sequencer (shared across all page sets)
        self.bottom_row = self.page_button_sets[0].bottom_row
        self.right_column = self.page_button_sets[0].right_column

        self.bottom_row[0].press_action = self.enter_tempo_mode
        self.bottom_row[1].press_action = self.enter_tempo_mode

        for button in self.bottom_row[4:]:
            button.press_action = self.pages_callback

        for button in self.right_column:
            button.press_action = self.mute_callback

        # Add a button set for tempo mode
        self.tempo_button_set = self.apc.add_button_set(
            right_column=self.right_column
        )
        self.tempo_button_set.bottom_row[0].press_action = self.increase_tempo
        self.tempo_button_set.bottom_row[0].hold_action = self.increase_tempo_hold
        self.tempo_button_set.bottom_row[0].hold_time = 0.2

        self.tempo_button_set.bottom_row[1].press_action = self.decrease_tempo
        self.tempo_button_set.bottom_row[1].hold_action = self.decrease_tempo_hold
        self.tempo_button_set.bottom_row[1].hold_time = 0.2

        for button in self.tempo_button_set.bottom_row[4:]:
            button.press_action = self.pages_callback

        self.display_tempo()

        for slider in self.apc.sliders:
            slider.value_change_action = self.sliders_callback

        self.midi_out = rtmidi.MidiOut()
        self.midi_out.open_virtual_port("sequencer")

        self.current_step = 0
        self.prev_step = None
        self.is_playing = False

        # Initialize mute states - all unmuted to start
        self.muted_rows = [False for _ in range(8)]

        # With my setup where the APC Mini is connected to a powered docking
        # station, the lights will stay on even when the laptop is standby
        atexit.register(self.lights_out)

        # We need to wait a bit for the MIDI to get wired up
        time.sleep(0.25)

        # Initialize all grid buttons to off
        for button in self.apc.grid:
            button.light("off")

        # Light up right column (mute buttons)
        for button in self.right_column:
            button.light("on")

        # Set initial page indicator
        self.select_page(0)
        for i in range(4, 8):
            self.bottom_row[i].light("off")
        self.bottom_row[4 + self.current_page].light("green")

    def light_column(self, page, column, active):
        buttons = self.page_button_sets[page].grid_columns[column]
        for button in buttons:
            if active:
                if button.is_on:
                    button.light("red")
                elif not button.is_on:
                    button.light("green")
            else:
                if button.is_on:
                    button.light("orange")
                elif not button.is_on:
                    button.light("off")

    def enter_tempo_mode(self, control):
        self.current_page = -1
        self.apc.activate_button_set(self.tempo_button_set)

    def increase_tempo(self, control):
        self.tempo = min(300, self.tempo + 1)
        self.clock.tempo = self.tempo / 60
        self.display_tempo()

    def increase_tempo_hold(self, control):
        while control.held:
            self.tempo = min(300, self.tempo + 1)
            self.clock.tempo = self.tempo / 60
            self.display_tempo()
            time.sleep(0.1)
        self.display_tempo()

    def decrease_tempo(self, control):
        self.tempo = max(20, self.tempo - 1)
        self.clock.tempo = self.tempo / 60
        self.display_tempo()

    def decrease_tempo_hold(self, control):
        while control.held:
            self.tempo = max(20, self.tempo - 1)
            self.clock.tempo = self.tempo / 60
            self.display_tempo()
            time.sleep(0.1)
        self.display_tempo()

    def display_tempo(self):
        tempo_str = str(int(round(self.tempo)))
        self.tempo_button_set.render_digits(tempo_str)

    def grid_callback(self, control):
        if self.tempo_mode:
            return
        # Toggle sequence state
        control.is_on = not control.is_on
        control.light("orange" if control.is_on else "off")

    def mute_callback(self, control):
        # Handle mute buttons
        row_idx = control.number - 82
        self.muted_rows[row_idx] = not self.muted_rows[row_idx]
        new_state = "off" if self.muted_rows[row_idx] else "green"
        control.light(new_state)

    def pages_callback(self, control):
        self.select_page(control.number - 68)

    def sliders_callback(self, control):
        # Pass through the sliders. This isn't so efficient, but
        # it's an easy way to prevent the note messages for the grid
        # from leaking through
        self.midi_out.send_message([0xB0, control.number, control.value * 127])

    def select_page(self, page):
        if page != self.current_page:
            old_page_index = self.current_page

            self.current_page = page
            self.apc.activate_button_set(self.page_button_sets[page])

            # Update page indicator lights
            for i in range(4, 8):
                self.bottom_row[i].light("off")
            self.bottom_row[4 + page].light("red")

    def lights_out(self):
        for button in self.apc.buttons:
            button.light("off")

    def play(self):
        if not self.is_playing:
            routine_class = getattr(self.clock, "routine_class", None)
            if routine_class is None:
                from sc3.base.all import Routine

                routine_class = Routine
            self.routine = routine_class(self.tick)
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


def main():
    from sc3.all import TempoClock

    clock = TempoClock(120 / 60)
    seq = Sequencer(clock, steps_per_beat=4, total_pages=4)
    seq.play()


if __name__ == "__main__":
    main()
