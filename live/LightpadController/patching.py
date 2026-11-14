"""Input catalog and routing patches stored in Live, not in a sidecar.

Empty [Patch] tracks are routing/monitor configuration. They are never take
destinations, and an existing track with clips is never repurposed or deleted.
Native route identifiers remain opaque; only actual offered routes are written.
"""
from dataclasses import dataclass
import re
import time

from .live_state import same

PATCH_PREFIX = '[Patch]'
RETURN_PREFIX = '[Return]'
MONITOR_IN, MONITOR_OFF = 0, 2
LIMIT = 25


class PendingRouting(Exception):
    """A Native routing write is awaiting a later display callback."""


class PreparationJob:
    def __init__(self, iterator, guard=None, clock=time.monotonic):
        self.iterator, self.guard, self.clock = iterator, guard, clock
        self.done, self.error, self.result = False, None, None
        self.started, self.generations = clock(), 0
        self._cancel = None
        self._unwinding = False

    def cancel(self, reason='routing preparation cancelled'):
        if not self.done:
            self._cancel = ValueError(reason)

    def advance(self):
        if self.done:
            return
        self.generations += 1
        try:
            error = self._cancel
            self._cancel = None
            if not self._unwinding:
                if error is None and (self.clock() - self.started > 10 or self.generations > 512):
                    error = TimeoutError('Native routing preparation did not settle')
                if error is None and self.guard is not None:
                    try:
                        self.guard()
                    except Exception as changed:
                        error = changed
            if error is not None:
                self._unwinding = True
                self.iterator.throw(error)
            else:
                next(self.iterator)
        except StopIteration as stopped:
            self.result, self.done, self.guard = stopped.value, True, None
        except Exception as failed:
            self.error, self.done, self.guard = failed, True, None


def _choice_steps(read, predicate, description):
    while True:
        matches = [route for route in read() if predicate(route)]
        if len(matches) > 1:
            raise ValueError(description + ' is ambiguous')
        if matches:
            return matches[0]
        yield


def _route_steps(track, direction, kind, value):
    attr = direction + '_routing_' + kind
    if route_equal(getattr(track, attr), value):
        return
    # Consume this property write once. Channel offers and readback can remain
    # stale until Live returns to its event loop; never replay it as a toggle.
    setattr(track, attr, value)
    yield
    if not route_equal(getattr(track, attr), value):
        raise ValueError('Live did not confirm ' + attr)


def _channel_steps(track, direction, name):
    clean = lambda text: text.casefold().replace(' ', '').replace('-', '')
    return (yield from _choice_steps(
        lambda: getattr(track, 'available_' + direction + '_routing_channels'),
        lambda route: clean(label(route)) == clean(name), name + ' routing channel'))


def _instrument_steps(track):
    while True:
        endpoints = tuple(route for route in track.available_output_routing_channels
                          if label(route).casefold().replace(' ', '') != 'trackin' and label(route))
        first = tuple(route for route in endpoints if re.match(r'^1\s*-', label(route)))
        offered = first or endpoints
        if len(offered) > 1:
            raise ValueError('instrument channel one is ambiguous')
        if offered:
            return offered[0]
        yield


def route_field(route, name):
    return route.get(name) if isinstance(route, dict) else getattr(route, name, None)


def route_equal(left, right):
    a, b = route_field(left, 'identifier'), route_field(right, 'identifier')
    if a is not None and b is not None:
        return a == b
    # Python Remote Script routing values are Native objects, unlike the LOM
    # dictionaries. Their offered value/readback equality is the identity seam.
    return left is not None and right is not None and same(left, right)


def label(route):
    return str(route_field(route, 'display_name') or '')


def choose(routes, predicate, description):
    matches = [route for route in routes if predicate(route)]
    if len(matches) != 1:
        raise ValueError(description + ' is unavailable or ambiguous')
    return matches[0]


def channel(track, direction, name):
    clean = lambda text: text.casefold().replace(' ', '').replace('-', '')
    return choose(getattr(track, 'available_' + direction + '_routing_channels'),
                  lambda route: clean(label(route)) == clean(name), name + ' routing channel')


def set_route(track, direction, kind, value):
    attr = direction + '_routing_' + kind
    setattr(track, attr, value)
    if not route_equal(getattr(track, attr), value):
        raise ValueError('Live did not confirm ' + attr)


def instrument_channel(track):
    endpoints = tuple(route for route in track.available_output_routing_channels
                      if label(route).casefold().replace(' ', '') != 'trackin')
    first = tuple(route for route in endpoints if re.match(r'^1\s*-', label(route)))
    return choose(first or endpoints, lambda route: True, 'instrument channel one')


@dataclass(frozen=True)
class Source:
    index: int
    track: object
    midi: bool

    @property
    def name(self):
        return str(self.track.name)


class Patching:
    def __init__(self, song, state):
        self.song, self.state = song, state
        self._frozen = None
        self._pending = []
        self.issues = {}
        self._jobs = []
        self._control_job = None
        self._layer_jobs = []

    def needs_preparation(self):
        return any(not job.done for job in self._jobs)

    def advance_preparations(self):
        # The facade calls this once at the start of update_display, never from
        # dispatch/touch callbacks. Jobs created during this pass wait one more.
        for job in tuple(self._jobs):
            job.advance()
        self._jobs[:] = [job for job in self._jobs if not job.done]

    def _enqueue(self, iterator, guard=None):
        job = PreparationJob(iterator, guard)
        self._jobs.append(job)
        return job

    def _control(self, iterator, guard=None):
        if self._control_job is not None and not self._control_job.done:
            iterator.close()
            raise PendingRouting('another routing control is preparing')
        self._control_job = self._enqueue(iterator, guard)
        return self._control_job

    def start_toggle(self, group, source_index):
        self.state.refresh()
        source = self.source(source_index)
        parent = self.state.groups[group].group
        adding = not any(same(item.track, source.track) for item in self.selected(group))
        def guard():
            self.state.refresh()
            self.validate(source)
            if group not in self.state.groups or not same(self.state.groups[group].group, parent):
                raise ValueError('remote group changed during routing preparation')
        return self._control(self._toggle_steps(group, source, adding), guard)

    def start_monitor(self, group):
        self.state.refresh()
        parent = self.state.groups[group].group if group is not None else None
        selected = self.selected(group) if group is not None else ()
        def guard():
            self.state.refresh()
            if group is not None and (group not in self.state.groups
                    or not same(self.state.groups[group].group, parent)):
                raise ValueError('remote group changed during routing preparation')
            for source in selected:
                self.validate(source)
        return self._control(self._monitor_steps(group, selected), guard)

    def start_layer(self, track, source):
        for old_track, old_source, old_job in self._layer_jobs:
            if same(old_track, track) and not old_job.done:
                old_job.iterator.close()
                old_job.done, old_job.error = True, ValueError('layer preparation superseded')
        def guard():
            self.validate(source)
            if not any(same(track, current) for current in self.song.tracks):
                raise ValueError('recording destination disappeared during preparation')
        job = self._enqueue(self._layer_steps(track, source), guard)
        self._layer_jobs[:] = [item for item in self._layer_jobs if not same(item[0], track)]
        self._layer_jobs.append((track, source.track, job))
        return job

    def cancel_layer(self, track):
        for old_track, source, job in self._layer_jobs:
            if same(old_track, track) and not job.done:
                job.iterator.close()
                job.done, job.error = True, ValueError('layer preparation cancelled')
                job.guard = None
        self._layer_jobs[:] = [item for item in self._layer_jobs if not same(item[0], track)]

    def forget_layer(self, track, source):
        self._layer_jobs[:] = [item for item in self._layer_jobs
                              if not (same(item[0], track) and same(item[1], source.track) and item[2].done)]

    def layer_ready(self, track, source):
        job = next((job for old_track, old_source, job in self._layer_jobs
                    if same(track, old_track) and same(source.track, old_source)), None)
        if job is None or not job.done:
            raise PendingRouting('recording input routing is preparing')
        if job.error is not None:
            raise job.error
        self.validate_layer(track, source)
        return True

    def input_groups(self):
        return tuple(track for track in self.song.tracks if bool(track.is_foldable)
                     and re.match(r'^\[Inputs\](?:\s|$)', str(track.name)))

    @property
    def enabled(self):
        # A duplicate marker is not authorization to fall back to external input.
        return bool(self.input_groups())

    def _leaves(self):
        roots = self.input_groups()
        if len(roots) != 1:
            return ()
        return tuple(track for track in self.song.tracks if not bool(track.is_foldable)
                     and self.state._owner(track, {0: roots[0]}) == 0
                     and (bool(getattr(track, 'has_audio_input', False))
                          or bool(getattr(track, 'has_midi_input', False))))

    def catalog(self):
        if self._frozen is not None:
            return self._frozen
        return tuple(Source(index, track, bool(getattr(track, 'has_midi_input', False)))
                     for index, track in enumerate(self._leaves()[:LIMIT]))

    def freeze(self):
        if self._frozen is None:
            self._frozen = self.catalog()

    def thaw(self):
        self._frozen = None

    def source(self, index):
        catalog = self.catalog()
        if type(index) is not int or not 0 <= index < len(catalog):
            raise ValueError('input source is unavailable')
        source = catalog[index]
        self.validate(source)
        return source

    def validate(self, source):
        if (not any(same(source.track, track) for track in self._leaves())
                or bool(getattr(source.track, 'is_frozen', False))):
            raise ValueError('input source changed or is frozen')
        if sum(str(track.name) == source.name for track in self.song.tracks) != 1:
            raise ValueError('routing track name is ambiguous')
        if source.midi and label(source.track.input_routing_type).casefold().replace(' ', '') == 'noinput':
            raise ValueError('prepared MIDI source has no input')
        if source.midi:
            # Physical-input inheritance bypasses top-level MIDI effects before
            # the instrument. A direct rack/instrument endpoint preserves its
            # own internal chain; effects after it still process its audio.
            instrument = False
            for device in source.track.devices:
                try:
                    kind = int(device.type)
                except (AttributeError, TypeError, ValueError):
                    raise ValueError('prepared MIDI device type is unavailable')
                if kind == 4:
                    raise ValueError('MIDI effects before the prepared instrument are unsupported')
                if kind == 1:
                    instrument = True
                    break
                if kind not in (2,):
                    raise ValueError('prepared MIDI device type is unsupported')
            if not instrument:
                raise ValueError('prepared MIDI source has no instrument')

    def _track_route(self, receiver, direction, track):
        # Native routes carry opaque identifiers. A unique offered display name
        # is required; duplicate track names never silently choose a destination.
        if sum(str(candidate.name) == str(track.name) for candidate in self.song.tracks) != 1:
            raise ValueError('routing track name is ambiguous')
        return choose(getattr(receiver, 'available_' + direction + '_routing_types'),
                      lambda route: label(route) == str(track.name), 'track routing')

    def _track_route_steps(self, receiver, direction, track):
        if sum(str(candidate.name) == str(track.name) for candidate in self.song.tracks) != 1:
            raise ValueError('routing track name is ambiguous')
        return (yield from _choice_steps(
            lambda: getattr(receiver, 'available_' + direction + '_routing_types'),
            lambda route: label(route) == str(track.name), 'track routing'))

    def routed_source(self, track, direction='input'):
        selected = getattr(track, direction + '_routing_type', None)
        for source in self.catalog():
            try:
                route = self._track_route(track, direction, source.track)
                if route_equal(route, selected):
                    return source
            except (AttributeError, RuntimeError, ValueError, IndexError):
                continue
        return None

    @staticmethod
    def quiet_patch(track):
        return (str(track.name).startswith(PATCH_PREFIX)
                and not bool(getattr(track, 'arm', False))
                and track.playing_slot_index in (-1, -2) and track.fired_slot_index == -1)

    def patch_tracks(self, group):
        item = self.state.groups.get(group)
        if item is None:
            return ()
        return tuple(track for track in item.tracks if self.quiet_patch(track))

    def selected(self, group):
        selected = []
        for track in self.patch_tracks(group):
            source = self.routed_source(track, 'output' if track.has_midi_input else 'input')
            try:
                valid = source is not None
                if valid and source.midi:
                    valid = (label(track.input_routing_type).casefold().replace(' ', '') != 'noinput'
                             and route_equal(track.output_routing_channel, instrument_channel(track)))
                elif valid:
                    valid = route_equal(track.input_routing_channel, channel(track, 'input', 'Post FX'))
            except (AttributeError, RuntimeError, ValueError, IndexError):
                valid = False
            if valid and not any(same(source.track, old.track) for old in selected):
                selected.append(source)
        return tuple(selected)

    def owners(self, source):
        if not source.midi:
            return 0
        owners = 0
        for index, group in self.state.groups.items():
            for item in group.playing:
                if bool(item.clip.is_audio_clip):
                    continue
                destination = self.routed_source(item.track, 'output')
                if destination is not None and same(destination.track, source.track):
                    owners |= 1 << index
        return owners

    def monitor(self):
        active = set()
        for group in self.state.groups:
            for patch in self.patch_tracks(group):
                source = self.routed_source(patch, 'output' if patch.has_midi_input else 'input')
                if source is None:
                    continue
                if source.midi:
                    if getattr(patch, 'current_monitoring_state', None) == MONITOR_IN:
                        active.add(group)
                elif getattr(source.track, 'current_monitoring_state', None) == MONITOR_IN:
                    try:
                        if self._return_for(source, group) is not None:
                            active.add(group)
                    except (AttributeError, RuntimeError, ValueError):
                        pass
        return next(iter(active)) if len(active) == 1 else None

    def snapshot(self):
        self.state.refresh()
        sources = []
        for source in self.catalog():
            color = int(source.track.color)
            available = any(same(source.track, track) for track in self._leaves()) and not bool(
                getattr(source.track, 'is_frozen', False))
            available = available and sum(str(track.name) == source.name for track in self.song.tracks) == 1
            try:
                self.validate(source)
            except (AttributeError, RuntimeError, ValueError, IndexError):
                available = False
            sources.append(dict(index=source.index, name=source.name, track=source.track,
                colour=tuple(round(((color >> shift) & 255) * 127 / 255) for shift in (16, 8, 0)),
                midi=source.midi, owners=self.owners(source), available=available))
        return dict(sources=tuple(sources), assignments=tuple(
            sum(1 << source.index for source in self.selected(group)) for group in range(8)),
            monitor=self.monitor())

    def _new_patch_track_steps(self, group, source):
        item = self.state.groups.get(group)
        if item is None:
            raise ValueError('remote group is unavailable')
        for parent, prepared_source, candidate in self._pending:
            if (same(parent, item.group) and same(prepared_source, source.track)
                    and any(same(candidate, child) for child in item.tracks)
                    and self.quiet_patch(candidate)
                    and not any(slot.has_clip or slot.is_triggered for slot in candidate.clip_slots)):
                return candidate
        before = tuple(self.song.tracks)
        position = next(i for i, track in enumerate(before) if same(track, item.group)) + 1
        create = self.song.create_midi_track if source.midi else self.song.create_audio_track
        create(position)
        created = [track for track in self.song.tracks if not any(same(track, old) for old in before)]
        if len(created) != 1:
            raise ValueError('Live did not create one patch track')
        track = created[0]
        track.name = PATCH_PREFIX + ' ' + source.name
        track.current_monitoring_state = MONITOR_OFF
        self._pending.append((item.group, source.track, track))
        yield
        self.state.refresh()
        if (not any(same(track, child) for child in self.state.groups[group].tracks)
                or any(slot.has_clip for slot in track.clip_slots)):
            raise ValueError('new patch track was not empty inside its remote group')
        return track

    def _input_steps(self, track, source):
        if source.midi:
            # Native PostFX taps stop forwarding live MIDI when the prepared
            # instrument track is Monitor Off. Inherit its physical controller
            # routing and play its instrument directly, without cloning it.
            for kind in ('type', 'channel'):
                original = getattr(source.track, 'input_routing_' + kind)
                options = getattr(track, 'available_input_routing_' + ('types' if kind == 'type' else 'channels'))
                selected = yield from _choice_steps(
                    lambda kind=kind: getattr(track, 'available_input_routing_' + ('types' if kind == 'type' else 'channels')),
                    lambda route: label(route) == label(original), 'prepared MIDI input ' + kind)
                yield from _route_steps(track, 'input', kind, selected)
        else:
            selected = yield from self._track_route_steps(track, 'input', source.track)
            yield from _route_steps(track, 'input', 'type', selected)
            selected = yield from _channel_steps(track, 'input', 'Post FX')
            yield from _route_steps(track, 'input', 'channel', selected)

    def _midi_output_steps(self, track, source):
        selected = yield from self._track_route_steps(track, 'output', source.track)
        yield from _route_steps(track, 'output', 'type', selected)
        # Track In feeds the source's monitoring stage and may form a MIDI echo.
        # Only one direct instrument endpoint is acceptable without more setup.
        selected = yield from _instrument_steps(track)
        yield from _route_steps(track, 'output', 'channel', selected)

    def _layer_steps(self, track, source):
        track.current_monitoring_state = MONITOR_OFF
        yield  # Track creation and its offered routes settle first.
        yield from self._input_steps(track, source)
        if source.midi:
            yield from self._midi_output_steps(track, source)
        else:
            # A user Audio Track default may route elsewhere. Recorded audio
            # must pass through the owning group's mute, volume and effects.
            owner = self.state._owner(track, {i: group.group for i, group in self.state.groups.items()})
            if owner is None:
                raise ValueError('audio recording destination has no remote group')
            selected = yield from self._track_route_steps(track, 'output', self.state.groups[owner].group)
            yield from _route_steps(track, 'output', 'type', selected)

    def configure_layer(self, track, source):
        return self.start_layer(track, source)

    def configure_input(self, track, source):
        return self._enqueue(self._input_steps(track, source))

    def configure_midi_output(self, track, source):
        return self._enqueue(self._midi_output_steps(track, source))

    def _new_patch_track(self, group, source):
        return self._enqueue(self._new_patch_track_steps(group, source))

    def validate_layer(self, track, source):
        self.validate(source)
        if source.midi:
            if (label(track.input_routing_type) != label(source.track.input_routing_type)
                    or label(track.input_routing_channel) != label(source.track.input_routing_channel)):
                raise ValueError('prepared MIDI input routing changed')
            destination = self.routed_source(track, 'output')
            if destination is None or not same(destination.track, source.track):
                raise ValueError('recording instrument routing changed')
            endpoint = instrument_channel(track)
            if not route_equal(track.output_routing_channel, endpoint):
                raise ValueError('recording instrument endpoint changed')
        else:
            observed = self.routed_source(track)
            if (observed is None or not same(observed.track, source.track)
                    or not route_equal(track.input_routing_channel, channel(track, 'input', 'Post FX'))):
                raise ValueError('recording input routing changed')

    def _return_for(self, source, group):
        for track in self.state.groups[group].tracks:
            if str(track.name).startswith(RETURN_PREFIX):
                route = self._track_route(source.track, 'output', track)
                if route_equal(source.track.output_routing_type, route):
                    return track
        return None

    def _audio_return_steps(self, source, group, created_returns=None):
        item = self.state.groups[group]
        name = RETURN_PREFIX + ' R%d ' % (group + 1) + source.name
        routed = self._return_for(source, group)
        candidates = [routed] if routed is not None else [track for track in item.tracks if str(track.name) == name]
        if len(candidates) > 1:
            raise ValueError('audio return is ambiguous')
        if candidates:
            receiver = candidates[0]
            if (receiver.arm or receiver.playing_slot_index not in (-1, -2)
                    or receiver.fired_slot_index != -1
                    or any(slot.has_clip or slot.is_triggered for slot in receiver.clip_slots)):
                raise ValueError('audio return contains user music; preserve it')
        else:
            before = tuple(self.song.tracks)
            position = next(i for i, track in enumerate(before) if same(track, item.group)) + 1
            self.song.create_audio_track(position)
            created = [track for track in self.song.tracks if not any(same(track, old) for old in before)]
            if len(created) != 1 or not same(created[0].group_track, item.group):
                raise ValueError('Live did not create an audio return in the remote group')
            receiver = created[0]
            if created_returns is not None:
                created_returns.append(receiver)
            # A user default can enable monitoring. Infrastructure must stay
            # quiet until No Input and the parent output have been confirmed.
            receiver.current_monitoring_state = MONITOR_OFF
            receiver.name = name
            yield
            self.state.refresh()
        off = yield from _choice_steps(lambda: receiver.available_input_routing_types,
                     lambda route: label(route).casefold().replace(' ', '') == 'noinput', 'return No Input')
        yield from _route_steps(receiver, 'input', 'type', off)
        selected = yield from self._track_route_steps(receiver, 'output', item.group)
        yield from _route_steps(receiver, 'output', 'type', selected)
        # Group output has only a blank channel in Native Live. Do not write a
        # Track In channel here; that applies to source -> receiver injection.
        receiver.current_monitoring_state = MONITOR_IN
        return receiver

    def _audio_to_group_steps(self, source, group, created_returns=None):
        receiver = yield from self._audio_return_steps(source, group, created_returns)
        selected = yield from self._track_route_steps(source.track, 'output', receiver)
        yield from _route_steps(source.track, 'output', 'type', selected)
        selected = yield from _channel_steps(source.track, 'output', 'Track In')
        yield from _route_steps(source.track, 'output', 'channel', selected)

    def set_monitor(self, group):
        return self.start_monitor(group)

    def _monitor_steps(self, group, selected=None):
        self.state.refresh()
        if group is not None and group not in self.state.groups:
            raise ValueError('remote group is unavailable')
        selected = (self.selected(group) if group is not None else ()) if selected is None else selected
        for source in selected:
            self.validate(source)
            if self.owners(source) & ~(1 << group):
                raise ValueError('instrument belongs to another playing group')
        # Monitoring changes touch existing sources, persistent assignments and
        # the destination's returns. Capture only those properties before any
        # write; a failure must not silence the previously working group.
        saved = []
        route_attrs = ('input_routing_type', 'input_routing_channel',
                       'output_routing_type', 'output_routing_channel')

        def remember(track, attrs):
            item = next((entry for entry in saved if same(entry[0], track)), None)
            if item is None:
                item = (track, {})
                saved.append(item)
            for attr in attrs:
                if attr not in item[1]:
                    item[1][attr] = getattr(track, attr)

        for source in self.catalog():
            remember(source.track, ('output_routing_type', 'output_routing_channel',
                                    'current_monitoring_state'))
        for index in self.state.groups:
            for track in self.patch_tracks(index):
                remember(track, route_attrs + ('current_monitoring_state',)
                         if index == group else ('current_monitoring_state',))
        if group is not None:
            for track in self.state.groups[group].tracks:
                if str(track.name).startswith(RETURN_PREFIX):
                    remember(track, route_attrs + ('current_monitoring_state',))
        created_returns = []
        try:
            yield from self._apply_monitor_steps(group, selected, created_returns)
        except Exception:
            # Newly allocated empty infrastructure is preserved for retry, but
            # must never remain a live input path after the action failed.
            failures = []
            for receiver in created_returns:
                try:
                    receiver.current_monitoring_state = MONITOR_OFF
                except Exception as error:
                    failures.append(str(error))
            # Routing type writes can reset their channel. Restore every type
            # before its channel, and enable the original monitoring only last.
            for attr in route_attrs + ('current_monitoring_state',):
                for track, properties in saved:
                    if attr not in properties:
                        continue
                    try:
                        original = properties[attr]
                        current = getattr(track, attr)
                        equal = route_equal(current, original) if attr in route_attrs else current == original
                        if not equal:
                            setattr(track, attr, original)
                            yield
                            current = getattr(track, attr)
                            equal = route_equal(current, original) if attr in route_attrs else current == original
                            if not equal:
                                raise ValueError('Live did not confirm restored ' + attr)
                    except Exception as error:
                        failures.append(str(error))
            if failures:
                self.issues['monitor_restore'] = '; '.join(failures)
            raise
        self.issues.pop('monitor_restore', None)

    def _apply_monitor_steps(self, group, selected, created_returns):
        for index in self.state.groups:
            for track in self.patch_tracks(index):
                track.current_monitoring_state = MONITOR_OFF
        for source in self.catalog():
            if any(same(source.track, track) for track in self._leaves()):
                source.track.current_monitoring_state = MONITOR_OFF
        if group is None:
            return
        for track in self.patch_tracks(group):
            source = self.routed_source(track, 'output' if track.has_midi_input else 'input')
            if source is None or not any(same(source.track, item.track) for item in selected):
                continue
            yield from self._audio_to_group_steps(source, group, created_returns)
            if source.midi:
                yield from self._input_steps(track, source)
                yield from self._midi_output_steps(track, source)
                track.current_monitoring_state = MONITOR_IN
            else:
                # Audio auditions directly into the selected Group Track, once.
                # The empty patch track remains configuration, not a second audio path.
                source.track.current_monitoring_state = MONITOR_IN

    def toggle(self, group, source_index):
        return self.start_toggle(group, source_index)

    def _toggle_steps(self, group, source, adding):
        self.state.refresh()
        if group not in self.state.groups:
            raise ValueError('remote group is unavailable')
        matches = [track for track in self.patch_tracks(group) if (
            (observed := self.routed_source(track, 'output' if track.has_midi_input else 'input')) is not None
            and same(observed.track, source.track))]
        if adding:
            matches = []
        if matches:
            for track in matches:
                if any(slot.has_clip or slot.is_triggered for slot in track.clip_slots):
                    raise ValueError('patch track contains user clips; preserve it')
                track.current_monitoring_state = MONITOR_OFF
                off = choose(track.available_input_routing_types,
                             lambda route: label(route).casefold().replace(' ', '') == 'noinput', 'No Input')
                yield from _route_steps(track, 'input', 'type', off)
                self._pending.append((self.state.groups[group].group, source.track, track))
        else:
            self.validate(source)
            if self.owners(source) & ~(1 << group):
                raise ValueError('instrument belongs to another playing group')
            old_patches = []
            if source.midi:
                for index in self.state.groups:
                    if index == group:
                        continue
                    for patch in self.state.groups[index].tracks:
                        if (not str(patch.name).startswith(PATCH_PREFIX)
                                or label(patch.input_routing_type).casefold().replace(' ', '') == 'noinput'):
                            continue
                        old_source = self.routed_source(patch, 'output')
                        if old_source is not None and same(old_source.track, source.track):
                            if (not self.quiet_patch(patch)
                                    or any(slot.has_clip or slot.is_triggered for slot in patch.clip_slots)):
                                raise ValueError('old instrument patch contains user music; preserve it')
                            old_patches.append(patch)
            track = yield from self._new_patch_track_steps(group, source)
            if source.midi:
                yield from self._midi_output_steps(track, source)
            # Publish the assignment through actual input routing only once
            # the prepared instrument destination has been verified.
            yield from self._input_steps(track, source)
            self._pending = [entry for entry in self._pending if not same(entry[2], track)]
            if old_patches:
                # Transfer only an idle instrument. Preflight every old route
                # before disconnecting any; no musical clip is stopped/deleted.
                off_routes = [(old, choose(old.available_input_routing_types,
                    lambda route: label(route).casefold().replace(' ', '') == 'noinput', 'No Input'))
                    for old in old_patches]
                saved = [(old, 'input_routing_type', old.input_routing_type) for old in old_patches]
                saved += [(old, 'input_routing_channel', old.input_routing_channel) for old in old_patches]
                monitors = [patch for index in self.state.groups for patch in self.patch_tracks(index)]
                saved += [(patch, 'current_monitoring_state', patch.current_monitoring_state) for patch in monitors]
                for prepared in self.catalog():
                    saved.extend((prepared.track, attr, getattr(prepared.track, attr)) for attr in
                                 ('output_routing_type', 'output_routing_channel', 'current_monitoring_state'))
                try:
                    for old, off in off_routes:
                        old.current_monitoring_state = MONITOR_OFF
                        yield from _route_steps(old, 'input', 'type', off)
                    self.state.refresh()
                    yield from self._monitor_steps(group)
                except Exception:
                    # The new intention is cancelled; actual old routes are
                    # restored where Native allows it. No automatic retry.
                    try:
                        off = choose(track.available_input_routing_types,
                            lambda route: label(route).casefold().replace(' ', '') == 'noinput', 'No Input')
                        track.current_monitoring_state = MONITOR_OFF
                        yield from _route_steps(track, 'input', 'type', off)
                    except Exception as error:
                        self.issues['transfer'] = str(error)
                    for previous, attr, value in saved:
                        try:
                            if not (route_equal(getattr(previous, attr), value)
                                    if 'routing' in attr else getattr(previous, attr) == value):
                                setattr(previous, attr, value)
                                yield
                        except Exception as error:
                            self.issues['transfer'] = str(error)
                    self._pending.append((self.state.groups[group].group, source.track, track))
                    raise
                for old in old_patches:
                    parent = old.group_track
                    self._pending.append((parent, source.track, old))
                return
        self.state.refresh()
        yield from self._monitor_steps(group)
