"""Temporary technical spike. The existing Lightpad probe owns its UI.

Channel 16 CC20: desired Track Activator state, strictly 0 or 127.
Channel 16 CC20 output: actual Track Activator state, derived from Track.mute.
CC21 is the pad's measurement acknowledgment and is deliberately ignored.
Only a unique, top-level Group Track named 'Feedback probe' is eligible.
"""

import Live
from _Framework.ControlSurface import ControlSurface


class LightpadIntegrationSpike(ControlSurface):
    TARGET_NAME = "Feedback probe"
    CHANNEL = 15
    CC = 20

    def __init__(self, c_instance):
        self._target = None
        self._last_sent = None
        self._feedback_count = 0
        self._closed = False
        self._tracks_callback = self._on_tracks_changed
        self._mute_callback = self._on_mute_changed
        self._name_callback = self._on_tracks_changed
        super(LightpadIntegrationSpike, self).__init__(c_instance)
        self.song().add_tracks_listener(self._tracks_callback)
        self._bind_target(force=True)

    def build_midi_map(self, midi_map_handle):
        super(LightpadIntegrationSpike, self).build_midi_map(midi_map_handle)
        Live.MidiMap.forward_midi_cc(
            self._c_instance.handle(), midi_map_handle, self.CHANNEL, self.CC
        )

    def receive_midi(self, midi_bytes):
        if self._closed or len(midi_bytes) != 3:
            return
        status, controller, value = midi_bytes
        if status != 0xB0 + self.CHANNEL or controller != self.CC or value not in (0, 127):
            return
        try:
            # Revalidate before every mutation, including after a track rename.
            self._bind_target()
            if self._target is None:
                return
            before = self._feedback_count
            self._target.mute = value == 0
            # A changed mute value may notify synchronously. Avoid a duplicate
            # reply, while still replying if the requested state was unchanged.
            if self._feedback_count == before:
                self._publish_state(force=True)
        except RuntimeError as error:
            self.log_message("Lightpad spike: command failed: %s" % error)
            self._detach_target()

    def refresh_state(self):
        super(LightpadIntegrationSpike, self).refresh_state()
        if not self._closed:
            self._bind_target(force=True)

    def _find_target(self):
        matches = [track for track in self.song().tracks
                   if track.is_foldable and not track.is_grouped
                   and track.name == self.TARGET_NAME]
        return matches[0] if len(matches) == 1 else None

    def _bind_target(self, force=False):
        if self._closed:
            return
        target = self._find_target()
        if target != self._target or force:
            self._detach_target()
            self._target = target
            self._last_sent = None
            if target is not None:
                target.add_mute_listener(self._mute_callback)
                target.add_name_listener(self._name_callback)
                self.show_message("Lightpad spike: Feedback probe connected")
                self.log_message("Lightpad spike: bound Feedback probe")
            else:
                self.show_message("Lightpad spike: no unique Feedback probe group")
                self.log_message("Lightpad spike: no unique top-level Feedback probe; control disabled")
        self._publish_state(force=force)

    def _publish_state(self, force=False):
        if self._closed or self._target is None:
            return
        value = 0 if self._target.mute else 127
        if force or value != self._last_sent:
            self._send_midi((0xB0 + self.CHANNEL, self.CC, value), optimized=False)
            self._last_sent = value
            self._feedback_count += 1
            self.log_message("Lightpad spike: Feedback probe actual active=%d" % (value == 127))

    def _on_mute_changed(self):
        self._publish_state()

    def _on_tracks_changed(self):
        self._bind_target()

    def _detach_target(self):
        if self._target is not None:
            for method, callback in [("remove_mute_listener", self._mute_callback),
                                     ("remove_name_listener", self._name_callback)]:
                try:
                    getattr(self._target, method)(callback)
                except (RuntimeError, ValueError):
                    pass  # A removed Live object can already have lost its listeners.
        self._target = None

    def disconnect(self):
        self._closed = True
        self._detach_target()
        try:
            self.song().remove_tracks_listener(self._tracks_callback)
        except (RuntimeError, ValueError):
            pass
        super(LightpadIntegrationSpike, self).disconnect()
