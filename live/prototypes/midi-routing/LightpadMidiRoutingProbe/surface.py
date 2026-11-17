"""Mailbox-only scratch ControlSurface. Construction changes no Live state."""
import json
from pathlib import Path
import time

import Live
from _Framework.ControlSurface import ControlSurface

from .mailbox import Mailbox
from .probe import Probe, OPERATIONS


class MidiRoutingProbeSurface(ControlSurface):
    def __init__(self, c_instance):
        self._probe, self._mailbox, self._last_poll = None, None, 0.0
        super(MidiRoutingProbeSurface, self).__init__(c_instance)
        config = json.loads((Path(__file__).parent/'manifest.json').read_text())
        self.directory = Path(config['mailbox_directory'])
        states = Live.Track.Track.monitoring_states
        self._probe = Probe(self.song(), config, Live.Song.Quantization.q_bar,
                            {'off': states.OFF, 'auto': states.AUTO, 'in': states.IN}, self._emit)
        self._mailbox = Mailbox(self.directory, self._probe.dispatch, self.log_message,
                                operations=OPERATIONS)

    def _emit(self, event):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory/'events.jsonl').open('a') as stream:
            stream.write(json.dumps(event, sort_keys=True)+'\n')

    def receive_midi(self, message):
        pass

    def update_display(self):
        super(MidiRoutingProbeSurface, self).update_display()
        now = time.monotonic()
        if self._mailbox is None or now-self._last_poll < .25:
            return
        self._last_poll = now
        try:
            self._probe.tick()
            self._mailbox.poll()
        except Exception as exc:
            self._emit({'event': 'poll_error', 'type': type(exc).__name__, 'message': str(exc)[:500]})

    def disconnect(self):
        if self._probe is not None:
            self._probe.close()
        self._mailbox = None
        super(MidiRoutingProbeSurface, self).disconnect()
