"""Live input monitoring through retained recording destinations.

The recording adapter claims every admitted take before Native launch admission,
then claims a following take or spare reserve when Accept queues its boundary.
Call tick after recording cleanup and reserve preparation. No Track discovery,
source routing, device copying or parameter scanning happens in this module.
"""
from .bundle_recording import members
from .model import same, NATIVE_ERRORS, native_error

AUTO, OFF = 1, 2
_NATIVE_ERRORS = NATIVE_ERRORS


def input_audible(member, audible):
    # MIDI instruments provide their own sole live-input path. Arming a
    # following capture must never turn it into a second MIDI forwarder.
    return audible and getattr(getattr(member, 'source', None), 'kind', 'audio') != 'midi'


class DirectMonitoringError(ValueError):
    """One failed retained bundle; the backend may evict only these groups."""
    def __init__(self, reason, targets):
        super().__init__(reason)
        self.groups = tuple(sorted({group for unused, audible, group in targets}))
        self.tracks = tuple(track for track, audible, group in targets)


class DirectMonitoring:
    def __init__(self, backend):
        self.backend = backend
        # Retain Live objects and compare Native identity, never wrapper reprs.
        self._owned = []

    def owned(self, track):
        return any(same(track, old) for old, unused in self._owned)

    def recover_saved(self, track, group):
        """Retain a caller-validated saved lane without changing Native state.

        The catalog must first verify this lane's namespaced ownership marker,
        actual group parent, source UUID and actual Post FX source identity.
        Mere arm/monitor state is never proof of controller ownership. A manual
        or pending recording remains with the normal recording-adoption path.
        """
        if type(group) is not int or not 0 <= group < 8:
            return False
        try:
            if (not track.arm or track.current_monitoring_state not in (AUTO, OFF)
                    or track.is_frozen or self._recording(track)
                    or getattr(track, 'fired_slot_index', -1) != -1):
                return False
        except _NATIVE_ERRORS as error:
            native_error(error)
            self._forget(track)
            return False
        existing = next((old_group for old, old_group in self._owned if same(track, old)), None)
        if existing is not None:
            return existing == group
        self._owned.append((track, group))
        self._event('direct_monitor_recovered', track, group=group)
        return True

    def _forget(self, track):
        self._owned = [(old, group) for old, group in self._owned
                       if old is not track and not same(old, track)]

    def _event(self, name, track, **fields):
        try:
            label = str(track.name)
        except _NATIVE_ERRORS as error:
            native_error(error)
            label = 'unavailable'
        self.backend.events.append(dict(event=name, track=label, **fields))

    @staticmethod
    def _recording(track):
        row = track.playing_slot_index
        if row < 0:
            return False
        slots = track.clip_slots
        if row >= len(slots):
            return False
        slot = slots[row]
        return slot.has_clip and slot.clip.is_recording

    def _validate(self, track):
        """A controller must never adopt somebody else's armed destination."""
        try:
            if track.is_frozen:
                raise ValueError('monitor destination is frozen')
            if track.arm and not self.owned(track):
                raise ValueError('monitor destination armed externally')
            # Read both setters' current values before claiming any ownership.
            track.current_monitoring_state
        except _NATIVE_ERRORS as error:
            native_error(error)
            self._forget(track)
            self._event('direct_monitor_stale', track)
            return False
        return True

    def _claim(self, track, audible, group):
        if not self.owned(track):
            self._owned.append((track, group))
        writes = 0
        try:
            wanted = AUTO if audible else OFF
            if track.current_monitoring_state != wanted:
                track.current_monitoring_state = wanted
                writes += 1
            if not track.arm:
                track.arm = True
                writes += 1
            if not track.arm:
                raise ValueError('Live did not retain the recording arm')
        except _NATIVE_ERRORS as error:
            native_error(error)
            # Keep a live destination owned after an uncertain arm/mode write
            # so the next cleanup can still silence/disarm that destination.
            try:
                track.arm
            except _NATIVE_ERRORS as stale_error:
                native_error(stale_error)
                self._forget(track)
            self._event('direct_monitor_unavailable', track, reason=str(error))
            raise ValueError('monitor destination unavailable') from error
        if writes:
            self._event('direct_monitor_claim', track, mode=wanted, writes=writes)

    @staticmethod
    def _add(targets, track, audible, group):
        for index, (old, old_audible, old_group) in enumerate(targets):
            if same(old, track):
                targets[index] = (old, old_audible or audible, old_group)
                return
        targets.append((track, audible, group))

    def _disable_other_paths(self, targets, complete=False):
        """Finish old monitoring paths before enabling the new owner's inputs."""
        for track, group in tuple(self._owned):
            wanted = next((audible for other, audible, unused in targets if same(track, other)), None)
            if wanted or (wanted is None and not complete
                          and group == self.backend.audio_inputs.monitor_owner):
                continue
            try:
                if track.current_monitoring_state != OFF:
                    track.current_monitoring_state = OFF
            except _NATIVE_ERRORS as error:
                native_error(error)
                try:
                    track.arm
                except _NATIVE_ERRORS as error:
                    native_error(error)
                    self._forget(track)
                    self._event('direct_monitor_stale', track)
                    continue
                # An alive old path with an uncertain disable must not be
                # followed by enabling another owner's live input.
                self._event('direct_monitor_disable_unavailable', track, reason=str(error))
                raise ValueError('previous monitor path could not be disabled') from error

    def _contain_failed_targets(self, targets):
        """Retire only our quiet members of an unsuccessful bundle admission.

        An issued recording/trigger is owned by recording containment instead.
        Cleanup reads each setter back; an uncertain live handle stays owned
        until a later cleanup can verify it is both silent and disarmed.
        """
        for track, audible, group in targets:
            if not self.owned(track):
                continue
            try:
                if (track.playing_slot_index not in (-1, -2)
                        or getattr(track, 'fired_slot_index', -1) != -1):
                    continue
            except _NATIVE_ERRORS as error:
                native_error(error)
                self._forget(track)
                continue
            for name, wanted in (('current_monitoring_state', OFF), ('arm', False)):
                try:
                    if getattr(track, name) != wanted:
                        setattr(track, name, wanted)
                except _NATIVE_ERRORS as error:
                    native_error(error)
                    self._event('direct_monitor_cleanup_unavailable', track,
                                field=name, reason=str(error))
            try:
                if track.current_monitoring_state == OFF and not track.arm:
                    self._forget(track)
            except _NATIVE_ERRORS as error:
                native_error(error)
                self._forget(track)

    def _claim_targets(self, targets, complete=False):
        # Validate the whole admission before writes. A failure retires quiet
        # owned members even when some of them were already idle-monitored.
        try:
            for track, audible, group in targets:
                if not self._validate(track):
                    raise ValueError('monitor destination unavailable')
            self._disable_other_paths(targets, complete)
            for track, audible, group in targets:
                self._claim(track, audible, group)
        except (ValueError,) + _NATIVE_ERRORS as error:
            native_error(error)
            self._contain_failed_targets(targets)
            self._event('direct_monitor_bundle_failed', targets[0][0] if targets else None,
                        groups=sorted({group for unused, audible, group in targets}),
                        reason=str(error))
            raise DirectMonitoringError(str(error), targets) from error

    def claim_take(self, take):
        """Called after bundle attachment and before enqueue/_arm_members."""
        audible = take.group == self.backend.audio_inputs.monitor_owner
        items = tuple(item for item in members(take)
                      if not getattr(take, 'adopted', False) or self.owned(item.track))
        self._claim_targets([(item.track, input_audible(item, audible), take.group) for item in items])
        for item in items:
            if self.owned(item.track):
                item.armed_here = True

    def claim_reserve(self, reserve):
        """Allow the accepted, brief overlap at a queued handoff boundary."""
        audible = reserve.group == self.backend.audio_inputs.monitor_owner
        self._claim_targets([(item.track, input_audible(item, audible), reserve.group) for item in reserve.members])

    def tick(self):
        """Reconcile only retained captures and the owner's idle reserve."""
        take = self.backend.recording.take
        captures = []
        if take is not None and take.state not in ('completed', 'canceling'):
            captures.append(take)
            following = take.following
            if following is not None and following.state not in ('completed', 'canceling'):
                captures.append(following)
        owner = self.backend.audio_inputs.monitor_owner
        targets = []
        for capture in captures:
            for item in members(capture):
                if getattr(capture, 'adopted', False) and not self.owned(item.track):
                    continue
                self._add(targets, item.track, input_audible(item, capture.group == owner), capture.group)
        spare = self.backend.reserves.get(owner)
        owner_capture = next((capture for capture in captures if capture.group == owner), None)
        if spare is not None and (owner_capture is None or
                owner_capture.state == 'finish_queued' and owner_capture.following is None):
            for destination in spare.members:
                self._add(targets, destination.track, input_audible(destination, True), spare.group)
        self._claim_targets(targets, complete=True)
        for capture in captures:
            for item in members(capture):
                if self.owned(item.track):
                    item.armed_here = True
        for track, unused in tuple(self._owned):
            if any(same(track, wanted) for wanted, unused, group in targets):
                continue
            try:
                # A surviving recording belongs to recording containment. Keep
                # its arm, but _disable_other_paths has silenced live monitoring.
                if self._recording(track):
                    continue
                if track.arm:
                    track.arm = False
            except _NATIVE_ERRORS as error:
                native_error(error)
                try:
                    if track.arm:
                        self._event('direct_monitor_disarm_unavailable', track)
                        continue
                except _NATIVE_ERRORS as error:
                    native_error(error)
                    pass
                self._event('direct_monitor_stale', track)
            self._forget(track)

    def close(self):
        for track, unused in tuple(self._owned):
            try:
                if not self._recording(track) and track.arm:
                    track.arm = False
            except _NATIVE_ERRORS as error:
                native_error(error)
                pass
        self._owned = []
