"""Throwaway fixed-operation Native probe. Never imported by production."""
import math
from pathlib import Path
import time

from .selection_restore import SelectionRestoration, same

OPERATIONS = frozenset(('inspect', 'track', 'mixer', 'route', 'create',
    'create_return', 'create_clip', 'fire', 'stop', 'delete_clip',
    'prepare_pair', 'fire_pair', 'stop_pair', 'next_pair'))
ROUTES = frozenset(('input_routing_type', 'input_routing_channel',
                   'output_routing_type', 'output_routing_channel'))
PREFIX = '__MIDI85__ '


def read(obj, name):
    try:
        value = getattr(obj, name)
        return value if value is None or isinstance(value, (str, bool, int, float)) else str(value)
    except Exception as exc:
        return {'unavailable': type(exc).__name__, 'message': str(exc)[:200]}


def finite(value):
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError('finite number required')
    return value


class Probe:
    def __init__(self, song, manifest, quantum, monitor_states, emit):
        self.song, self.manifest = song, manifest
        self.quantum, self.monitor_states, self.emit = quantum, monitor_states, emit
        self.path = str(Path(manifest['scratch_set_path']).resolve())
        self.allowed = frozenset(manifest['allowed_existing_tracks'])
        self.created, self.clips, self.selectors = [], [], {}
        self.restores, self.pair = [], None
        self._selector = 0
        self.observations = []

    def all_tracks(self):
        return tuple(self.song.tracks) + tuple(self.song.return_tracks) + (self.song.master_track,)

    def guard(self):
        if str(Path(str(self.song.file_path)).resolve()) != self.path:
            raise ValueError('only the exact saved Midi Routing Trial fixture is writable')

    def track(self, name, write=False):
        found = [t for t in self.all_tracks() if t.name == name]
        if len(found) != 1:
            raise ValueError('missing or ambiguous track: ' + str(name))
        track = found[0]
        if write and name not in self.allowed and not any(same(track, t) for t in self.created):
            raise ValueError('track is outside explicit fixture allowlist')
        return track

    def slot(self, payload, write=False):
        track = self.track(payload['track'], write)
        row = payload.get('row', 0)
        if type(row) is not int or not 0 <= row < min(32, len(track.clip_slots)):
            raise ValueError('row outside bounded existing fixture slots')
        return track, track.clip_slots[row], row

    def route_value(self, track, prop, obj):
        native = getattr(obj, 'identifier', None)
        attached = getattr(obj, 'attached_object', None)
        token = next((key for key, entry in self.selectors.items()
                      if same(entry[0], track) and entry[1] == prop and same(entry[2], obj)), None)
        if token is None:
            self._selector += 1
            token = 'route-%d' % self._selector
            self.selectors[token] = (track, prop, obj)
        return {'identifier': token, 'native_identifier': None if native is None else str(native),
                'display_name': str(getattr(obj, 'display_name', obj)),
                'attached_object': None if attached is None else {
                    'id': str(getattr(attached, '_live_ptr', id(attached))),
                    'name': str(getattr(attached, 'name', attached))}}

    def inspect_track(self, track):
        result = {'name': str(track.name), 'id': str(getattr(track, '_live_ptr', id(track))),
                  'parent': str(track.group_track.name) if getattr(track, 'group_track', None) else None}
        for name in ('has_midi_input', 'has_audio_input', 'has_audio_output', 'has_midi_output',
                     'arm', 'mute', 'is_foldable', 'current_monitoring_state', 'is_frozen',
                     'playing_slot_index', 'fired_slot_index', 'input_meter_level',
                     'output_meter_level', 'output_meter_left', 'output_meter_right'):
            result[name] = read(track, name)
        result['devices'] = [{'name': str(d.name), 'class_name': str(d.class_name),
                              'can_have_chains': read(d, 'can_have_chains')} for d in track.devices]
        result['routing'] = {}
        for prop in sorted(ROUTES):
            try:
                choices = tuple(getattr(track, 'available_' + prop + 's'))
                result['routing'][prop] = {
                    'current': self.route_value(track, prop, getattr(track, prop)),
                    'available': [self.route_value(track, prop, obj) for obj in choices]}
            except Exception as exc:
                result['routing'][prop] = {'unavailable': type(exc).__name__, 'message': str(exc)[:200]}
        mixer = track.mixer_device
        result['volume'] = {key: read(mixer.volume, key) for key in ('value', 'min', 'max', 'is_enabled')}
        result['sends'] = [{'index': i, **{key: read(p, key) for key in ('name', 'value', 'min', 'max', 'is_enabled')}}
                           for i, p in enumerate(mixer.sends)]
        result['clips'] = []
        for row, slot in enumerate(tuple(track.clip_slots)[:32]):
            if slot.has_clip:
                clip = slot.clip
                clip_state = {'row': row, **{key: read(clip, key) for key in (
                    'name', 'is_audio_clip', 'is_midi_clip', 'is_recording', 'is_playing',
                    'start_time', 'playing_position', 'loop_start', 'loop_end', 'end_marker', 'length')}}
                if clip.is_midi_clip:
                    try:
                        span = min(max(float(clip.length), 1.0), 64.0)
                        notes = tuple(clip.get_notes(0.0, 0, span, 128))
                        clip_state.update(note_count=len(notes), note_time_span=span,
                                          notes_first32=[list(note) for note in notes[:32]])
                    except Exception as exc:
                        clip_state['notes_unavailable'] = type(exc).__name__ + ': ' + str(exc)[:200]
                result['clips'].append(clip_state)
        return result

    def snapshot(self, names=None):
        tracks = [self.track(name) for name in names] if names else self.all_tracks()
        if len(tracks) > 48:
            raise ValueError('fixture inspection limited to48 tracks')
        return {'path': str(self.song.file_path), 'playing': bool(self.song.is_playing),
                'beat': float(self.song.current_song_time), 'monotonic': time.monotonic(),
                'tempo': float(self.song.tempo), 'numerator': int(self.song.signature_numerator),
                'denominator': int(self.song.signature_denominator),
                'selected_track': str(self.song.view.selected_track.name),
                'tracks': [self.inspect_track(t) for t in tracks],
                'selection_restoration': [r.result() for r in self.restores]}

    def dispatch(self, payload):
        op = payload['op']
        if op == 'inspect':
            return {'ok': True, 'observation': self.snapshot(payload.get('tracks'))}
        self.guard()
        result = getattr(self, 'op_' + op)(payload)
        return {'ok': True, 'result': result, 'beat': float(self.song.current_song_time),
                'monotonic': time.monotonic()}

    def op_track(self, p):
        track = self.track(p['track'], True)
        prop = p['property']
        if prop == 'monitor':
            attr, value = 'current_monitoring_state', self.monitor_states[p['value']]
        elif prop in ('arm', 'mute') and type(p['value']) is bool:
            attr, value = prop, p['value']
        else:
            raise ValueError('only monitor off/auto/in, boolean arm/mute are writable')
        before = read(track, attr)
        setattr(track, attr, value)
        return {'before': before, 'after': read(track, attr)}

    def op_mixer(self, p):
        track = self.track(p['track'], True)
        if p['property'] == 'volume':
            param = track.mixer_device.volume
        elif p['property'] == 'send':
            index = p['index']
            if type(index) is not int or not 0 <= index < len(track.mixer_device.sends):
                raise ValueError('invalid send index')
            param = track.mixer_device.sends[index]
        else:
            raise ValueError('only volume/send values are writable; send.is_enabled is read-only')
        value = finite(p['value'])
        if not param.min <= value <= param.max:
            raise ValueError('value outside actual parameter range')
        before = param.value
        param.value = value
        return {'before': before, 'after': param.value, 'is_enabled': read(param, 'is_enabled')}

    def op_route(self, p):
        track, prop = self.track(p['track'], True), p['property']
        if prop not in ROUTES:
            raise ValueError('unsupported routing property')
        item = self.selectors.get(p['identifier'])
        if item is None or not same(item[0], track) or item[1] != prop:
            raise ValueError('selector was not observed for this track and property in this session')
        choices = tuple(getattr(track, 'available_' + prop + 's'))
        valid = [choice for choice in choices if same(choice, item[2])
                 and str(getattr(choice, 'display_name', choice)) == p['display_name']]
        if len(valid) != 1:
            raise ValueError('observed selector no longer uniquely offered')
        before = self.route_value(track, prop, getattr(track, prop))
        setattr(track, prop, valid[0])
        return {'before': before, 'after': self.route_value(track, prop, getattr(track, prop))}

    def op_create(self, p):
        if len(self.created) >= 12 or self.song.is_playing:
            raise ValueError('create requires stopped transport and fewer than12 probe tracks')
        index, kind = p['index'], p['kind']
        if type(index) is not int or not 0 <= index <= len(self.song.tracks) or kind not in ('audio', 'midi'):
            raise ValueError('bounded index and audio/midi kind required')
        name = PREFIX + str(p['name'])
        if len(name) > 64 or any(t.name == name for t in self.all_tracks()):
            raise ValueError('unique bounded probe track name required')
        before = tuple(self.song.tracks)
        restore = SelectionRestoration(self.song)
        try:
            getattr(self.song, 'create_' + kind + '_track')(index)
        except BaseException:
            restore.close()
            raise
        new = [t for t in self.song.tracks if not any(same(t, old) for old in before)]
        if len(new) != 1:
            restore.close()
            raise ValueError('uncertain creation; inspect without retry')
        track = new[0]
        self.created.append(track)
        restore.capture_created(new)
        self.restores.append(restore)
        track.name = name
        track.current_monitoring_state = self.monitor_states['off']
        return self.inspect_track(track)

    def op_create_return(self, p):
        if self.song.is_playing or len(self.created) >= 12 or len(self.song.return_tracks) >= 10:
            raise ValueError('return creation requires stopped bounded fixture')
        before = tuple(self.song.return_tracks)
        view = self.song.view
        selected = (view.selected_track, view.selected_scene, view.highlighted_clip_slot)
        self.song.create_return_track()
        new = [t for t in self.song.return_tracks if not any(same(t, old) for old in before)]
        if len(new) != 1:
            raise ValueError('uncertain Return creation; inspect without retry')
        track = new[0]
        self.created.append(track)
        if same(view.selected_track, track):
            view.selected_track, view.selected_scene, view.highlighted_clip_slot = selected
        track.name = PREFIX + str(p['name'])[:40]
        return self.inspect_track(track)

    def owned_clip(self, slot):
        if not slot.has_clip:
            return False
        return any(same(slot.clip, clip) for clip in self.clips)

    def op_create_clip(self, p):
        track, slot, row = self.slot(p, True)
        if not track.has_midi_input or slot.has_clip or self.song.is_playing:
            raise ValueError('empty MIDI slot and stopped fixture required')
        length = finite(p.get('length', 8.0))
        if not 1 <= length <= 32:
            raise ValueError('generator length must be1..32 beats')
        slot.create_clip(length)
        clip = slot.clip
        self.clips.append(clip)
        clip.name = PREFIX + 'Fixed input notes'
        clip.set_notes(tuple((pitch, float(i*2), .75, 96, False)
                             for i, pitch in enumerate((60, 64, 67, 72)) if i*2+.75 <= length))
        clip.looping = True
        return {'track': track.name, 'row': row, 'clip': clip.name, 'length': clip.length}

    def op_fire(self, p):
        track, slot, row = self.slot(p, True)
        if not slot.has_clip or not self.owned_clip(slot):
            raise ValueError('fire requires a probe-owned generator or observed recorded clip')
        slot.fire(launch_quantization=self.quantum)
        return {'track': track.name, 'row': row, 'issued': 'fire_q_bar'}

    def op_stop(self, p):
        track = self.track(p['track'], True)
        track.stop_all_clips(True)
        return {'track': track.name, 'issued': 'stop_quantized'}

    def op_delete_clip(self, p):
        track, slot, row = self.slot(p, True)
        if self.song.is_playing or not self.owned_clip(slot) or slot.clip.is_recording:
            raise ValueError('delete requires stopped probe-owned exact clip')
        clip = slot.clip
        slot.delete_clip()
        self.clips = [old for old in self.clips if not same(old, clip)]
        return {'track': track.name, 'row': row, 'deleted': True}

    def pair_slots(self, p, key='tracks'):
        names = p[key]
        if not isinstance(names, list) or not 1 <= len(names) <= 4 or len(set(names)) != len(names):
            raise ValueError('one to four distinct fixture tracks required')
        result = []
        for name in names:
            track, slot, row = self.slot(dict(track=name, row=p.get('row', 0)), True)
            if track.is_foldable or track.is_frozen:
                raise ValueError('unfrozen leaf track required')
            result.append((track, slot, row))
        return result

    def op_prepare_pair(self, p):
        if self.song.is_playing:
            raise ValueError('prepare_pair requires stopped transport')
        pair = self.pair_slots(p)
        if any(slot.has_clip for track, slot, row in pair):
            raise ValueError('all pair slots must be empty')
        midi_monitor = p.get('midi_monitor', 'auto')
        if midi_monitor not in ('off', 'auto'):
            raise ValueError('midi_monitor must be off or auto')
        for track, slot, row in pair:
            mode = midi_monitor if track.has_midi_input else 'auto'
            track.current_monitoring_state = self.monitor_states[mode]
            track.arm = True
        if not all(track.arm for track, slot, row in pair):
            raise ValueError('Live did not retain simultaneous arms; inspect exclusive_arm setup')
        self.pair = pair
        return {'tracks': [track.name for track, slot, row in pair], 'armed': True}

    def op_fire_pair(self, p):
        if not self.song.is_playing or self.pair is None or any(slot.has_clip for t, slot, row in self.pair):
            raise ValueError('playing transport and prepared empty pair required')
        self._issue(self.pair, 'start')
        return {'issued': 'pair_start_q_bar', 'members': len(self.pair)}

    def op_stop_pair(self, p):
        if not self.song.is_playing or self.pair is None:
            raise ValueError('playing pair required')
        if not all(slot.has_clip and slot.clip.is_recording for t, slot, row in self.pair):
            raise ValueError('every member must actually be recording')
        self._issue(self.pair, 'finish')
        return {'issued': 'pair_finish_q_bar', 'members': len(self.pair)}

    def op_next_pair(self, p):
        if not self.song.is_playing or self.pair is None:
            raise ValueError('playing pair required')
        old, new = self.pair, self.pair_slots(p)
        if (not all(slot.has_clip and slot.clip.is_recording for t, slot, row in old)
                or any(slot.has_clip for t, slot, row in new)
                or any(same(a, b) for a, s, r in old for b, ss, rr in new)):
            raise ValueError('recording old members and distinct empty following tracks required')
        midi_monitor = p.get('midi_monitor', 'auto')
        if midi_monitor not in ('off', 'auto'):
            raise ValueError('midi_monitor must be off or auto')
        for track, slot, row in new:
            mode = midi_monitor if track.has_midi_input else 'auto'
            track.current_monitoring_state = self.monitor_states[mode]
            track.arm = True
        if not all(track.arm for track, slot, row in old+new):
            raise ValueError('Live did not retain all handoff arms')
        self._issue(old, 'finish')
        self._issue(new, 'start')
        self.pair = new
        return {'issued': 'finish_then_start_q_bar', 'old': [t.name for t, s, r in old],
                'new': [t.name for t, s, r in new], 'atomic': False}

    def _issue(self, pair, action):
        for track, slot, row in pair:
            event = {'event': action+'_issued', 'track': track.name, 'row': row,
                     'beat': float(self.song.current_song_time), 'monotonic': time.monotonic()}
            self.emit(event)
            if slot.has_clip and not self.owned_clip(slot):
                self.clips.append(slot.clip)
            # Mark before Native call. Mailbox consumes before dispatch; failures never replay.
            self.observations.append({'track': track, 'slot': slot, 'row': row,
                                      'action': action, 'issued': event, 'state': None})
            slot.fire(launch_quantization=self.quantum)

    def tick(self):
        for restore in self.restores:
            if not restore.done:
                restore.tick()
        if str(self.song.file_path) != self.path:
            return
        for record in self.observations[-24:]:
            slot = record['slot']
            if not slot.has_clip:
                continue
            clip = slot.clip
            if not any(same(clip, old) for old in self.clips):
                self.clips.append(clip)
            state = (bool(clip.is_recording), bool(clip.is_playing), float(clip.start_time))
            if state != record['state']:
                record['state'] = state
                self.emit({'event': 'clip_observed', 'action': record['action'],
                           'track': record['track'].name, 'row': record['row'],
                           'recording': state[0], 'playing': state[1], 'start_time': state[2],
                           'beat': float(self.song.current_song_time), 'monotonic': time.monotonic()})

    def close(self):
        for restore in self.restores:
            restore.close()
