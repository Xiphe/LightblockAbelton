"""Disabled-at-start no-MIDI trial using the normal controller implementation."""

import json
import os
import time
import traceback

import Live
from _Framework.ControlSurface import ControlSurface

from . import configuration as config
from .audio_configuration import load_audio_configuration
from .controller_probe import ControllerProbe, OPERATIONS
from .proof_mailbox import Mailbox


class ControllerProbeSurface(ControlSurface):
    def __init__(self, c_instance):
        self._closed, self._probe, self._mailbox = False, None, None
        super(ControllerProbeSurface, self).__init__(c_instance)
        try:
            if not config.CONTROLLER_PROBE_DIRECTORY or not config.AUDIO_CONTROLLER_MANIFEST:
                raise ValueError("explicit probe directory and audio manifest required")
            manifest = load_audio_configuration(config.AUDIO_CONTROLLER_MANIFEST, config.SYNC_UI_EVIDENCE)
            self._probe = ControllerProbe(self.song(), manifest, Live.Song.Quantization.q_bar,
                Live.Track.Track.monitoring_states.OFF, emit=self._emit)
            self._mailbox = Mailbox(config.CONTROLLER_PROBE_DIRECTORY, self._probe.dispatch,
                self.log_message, operations=OPERATIONS)
            self._emit({"event": "controller_probe_loaded", "enabled": False})
        except Exception as error:
            self._probe = None
            self.log_message("Lightpad controller probe unavailable: %s: %s\n%s" % (
                type(error).__name__, error, traceback.format_exc()))

    def _emit(self, event):
        record = {"monotonic": time.monotonic(), "observation": event}
        encoded = json.dumps(record, sort_keys=True, allow_nan=False)
        self.log_message("Lightpad controller probe " + encoded)
        os.makedirs(config.CONTROLLER_PROBE_DIRECTORY, exist_ok=True)
        with open(os.path.join(config.CONTROLLER_PROBE_DIRECTORY, "controller-events.jsonl"), "a") as target:
            target.write(encoded + "\n")

    def receive_midi(self, message):
        pass

    def update_display(self):
        super(ControllerProbeSurface, self).update_display()
        if self._closed or self._probe is None or self._mailbox is None:
            return
        self._probe.poll()
        self._mailbox.poll()

    def disconnect(self):
        self._closed = True
        if self._probe is not None:
            self._probe.close()
        super(ControllerProbeSurface, self).disconnect()
