"""Disabled-at-start local-file proof mode, with no Lightpad port interaction."""

import json
import os
import time
import traceback

import Live
from _Framework.ControlSurface import ControlSurface

from .configuration import PROOF_DIRECTORY
from .proof_driver import AudioProofDriver
from .proof_mailbox import Mailbox


class AudioProofSurface(ControlSurface):
    def __init__(self, c_instance):
        self._closed = False
        self._driver = None
        self._mailbox = None
        super(AudioProofSurface, self).__init__(c_instance)
        try:
            with open(os.path.join(PROOF_DIRECTORY, "fixture-manifest.json")) as source:
                manifest = json.load(source)
            self._driver = AudioProofDriver(self.song(), manifest,
                Live.Song.Quantization.q_bar, Live.Track.Track.monitoring_states.OFF, self._emit)
            self._mailbox = Mailbox(PROOF_DIRECTORY, self._driver.dispatch, self._emit)
            self._emit("Audio proof loaded DISABLED; no MIDI ports, transport or monitoring writes")
        except Exception as error:
            self.log_message("Audio proof unavailable: %s: %s\n%s" % (
                type(error).__name__, str(error)[:500], traceback.format_exc()))
            self._driver = None

    def _emit(self, message):
        self.log_message(message)
        record = {"monotonic": time.monotonic(), "message": message}
        with open(os.path.join(PROOF_DIRECTORY, "proof-events.jsonl"), "a") as target:
            target.write(json.dumps(record, sort_keys=True) + "\n")

    def receive_midi(self, message):
        pass

    def update_display(self):
        super(AudioProofSurface, self).update_display()
        if self._closed or self._driver is None or self._mailbox is None:
            return
        self._driver.poll()
        self._mailbox.poll()

    def disconnect(self):
        self._closed = True
        if self._driver is not None:
            # No implicit stop/discard, including when Live disconnects mid-take.
            self._driver.close()
        super(AudioProofSurface, self).disconnect()
