"""Small explicit Native simulator for the production controller tests.

Native requests are queued until advance(); arbitrary song-time writes alone do
not fabricate recording. This is deterministic scheduling evidence, not Live or
physical timing proof.
"""
import math
from types import SimpleNamespace as NS


class Parameter:
    def __init__(self, value=0., low=0., high=1.):
        self.value, self.min, self.max = value, low, high
        self.is_enabled, self.is_quantized = True, False


class Clip:
    def __init__(self, anchor=0., length=8., phase=0., recording=False, playing=True, audio=True):
        self.start_time, self.loop_start, self.loop_end = anchor, 0., length
        self.playing_position, self.is_recording, self.is_playing = phase, recording, playing
        self.is_audio_clip, self.warping, self.looping = audio, True, True
        self.sample_rate, self.sample_length = 48000., 48000*120
        self.warp_markers = (NS(sample_time=0., beat_time=0.), NS(sample_time=120., beat_time=240.))


class Slot:
    def __init__(self, track, row):
        self.track, self.row = track, row
        self.clip, self.is_triggered = None, False
        self.pending = None
        self.fire_error = None

    @property
    def has_clip(self):
        return self.clip is not None

    def fire(self, launch_quantization):
        song = self.track.song
        song.calls.append(('fire', self.track, self.row, launch_quantization))
        if self.fire_error == 'before':
            raise RuntimeError('Native fire refused')
        period = {4: song.bar_length, 5: 2., 7: 1., 9: .5, 11: .25}[int(launch_quantization)]
        origin = song.bar_origin if int(launch_quantization) == 4 else 0.
        target = origin + (math.floor((song.current_song_time-origin)/period+1e-9)+1)*period
        kind = 'finish' if self.has_clip and self.clip.is_recording else 'start'
        self.pending, self.is_triggered = (kind, target), True
        self.track.fired_slot_index = self.row
        if self.fire_error == 'after':
            raise RuntimeError('Native fire outcome ambiguous')

    def delete_clip(self):
        self.track.song.calls.append(('delete', self.track, self.row))
        self.clip = None

    def apply(self, now):
        if self.pending is None or now < self.pending[1]:
            return
        kind, target = self.pending
        self.pending, self.is_triggered = None, False
        self.track.fired_slot_index = -1
        self.track.playing_slot_index = self.row
        if kind == 'start':
            self.clip = Clip(anchor=target, recording=True)
        else:
            self.clip.loop_end = target-self.clip.start_time
            self.clip.start_time = target
            self.clip.is_recording = False
            self.clip.is_playing = True


class Track:
    def __init__(self, song, name, group=None, foldable=False, audio=True):
        self.song, self.name, self.group_track = song, name, group
        self.is_foldable, self.has_audio_input, self.has_midi_input = foldable, audio and not foldable, not audio
        self.is_frozen, self.arm, self.implicit_arm = False, False, False
        self.mute, self.color = False, 0x31a1c2
        self.playing_slot_index = self.fired_slot_index = -1
        self.clip_slots = [] if foldable else [Slot(self, i) for i in range(len(song.scenes))]
        self.input_routing_type, self.input_routing_channel = 'Factory Input', 'Factory Channel'
        self.output_routing_type, self.current_monitoring_state = 'Factory Output', 'Factory Monitor'
        self.devices = []
        self.mixer_device = NS(volume=Parameter(.75), panning=Parameter(0, -1, 1),
                               sends=[Parameter(.1*i) for i in range(4)])
        self.stop_pending = False
        self._data = {}

    def get_data(self,key,default):return self._data.get(key,default)
    def set_data(self,key,value):
        self.song.calls.append(('set_data',self,key,value))
        self._data[key]=value

    def stop_all_clips(self, quantized):
        self.song.calls.append(('stop', self, quantized))
        self.stop_pending = True
        if not self.song.defer_stop:
            self.quiet()

    def quiet(self):
        self.stop_pending = False
        self.playing_slot_index = self.fired_slot_index = -1
        for slot in self.clip_slots:
            slot.pending, slot.is_triggered = None, False
            if slot.has_clip:
                slot.clip.is_playing = slot.clip.is_recording = False


class Song:
    def __init__(self, groups=2, layers=0, scenes=2):
        self.current_song_time, self.tempo, self.is_playing = 1., 120., True
        self.signature_numerator, self.signature_denominator, self.metronome = 4, 4, False
        self.bar_origin = 0.
        self.scenes = [object() for unused in range(scenes)]
        self.tracks, self.calls, self.return_tracks = [], [], [object() for unused in range(4)]
        self.view = NS(selected_track=None)
        self.defer_stop, self.misplace_new_track, self.create_error = False, False, False
        self.file_path = '/some/saved/performance.als'
        for g in range(groups):
            group = Track(self, '[R%d] Music' % (g+1), foldable=True)
            self.tracks.append(group)
            for n in range(layers):
                self.tracks.append(Track(self, 'Layer %d' % n, group))

    @property
    def bar_length(self):
        return self.signature_numerator*4./self.signature_denominator

    def get_current_beats_song_time(self):
        length = self.bar_length
        relative = self.current_song_time-self.bar_origin
        bars = math.floor(relative/length)+1
        phase = relative%length
        # Real BeatTime supports the current denominator's note and16th ticks.
        beat_len = 4./self.signature_denominator
        beats = math.floor(phase/beat_len)+1
        fine = phase-(beats-1)*beat_len
        sub = math.floor(fine*4+1e-8)+1
        ticks = math.floor((fine-(sub-1)/4)*240+1e-7)+1
        return NS(bars=bars, beats=beats, sub_division=sub, ticks=ticks)

    def create_scene(self, index):
        self.calls.append(('create_scene', index))
        self.scenes.append(object())
        for track in self.tracks:
            if not track.is_foldable:
                track.clip_slots.append(Slot(track, len(track.clip_slots)))

    def create_audio_track(self, index):
        self.calls.append(('create_track', index))
        if self.create_error:
            raise RuntimeError('Live refused new track')
        parent = self.tracks[index-1] if index else None
        if parent is not None and not parent.is_foldable:
            parent = parent.group_track
        if self.misplace_new_track:
            parent = None
        self.tracks.insert(index, Track(self, 'Audio', parent))

    def advance(self, position, apply=True):
        self.current_song_time = float(position)
        for track in self.tracks:
            if apply:
                for slot in track.clip_slots:
                    slot.apply(position)
            for slot in track.clip_slots:
                if slot.has_clip and slot.clip.is_playing:
                    clip = slot.clip
                    clip.playing_position = (position-clip.start_time if clip.is_recording else
                                             clip.loop_start+(position-clip.start_time)%(clip.loop_end-clip.loop_start))

    def add_clip(self, group=0, row=0, length=8., phase=0., recording=False, playing=True, audio=True):
        parent = next(t for t in self.tracks if t.name.startswith('[R%d]' % (group+1)))
        track = Track(self, 'Manual', parent, audio=audio)
        self.tracks.insert(self.tracks.index(parent)+1, track)
        while len(track.clip_slots) <= row:
            self.create_scene(-1)
        clip = Clip(self.current_song_time-phase, length, phase, recording, playing, audio)
        track.clip_slots[row].clip = clip
        if playing:
            track.playing_slot_index = row
        return track, track.clip_slots[row], clip

# The helpers below add only explicit shape/selection/routing instrumentation.
BaseSong = Song

class View:
    def __init__(self,song,track):
        self.song=song;self._track=track;self.selected_scene=song.scenes[0]
        self.highlighted_clip_slot=track.clip_slots[0] if track.clip_slots else None
        self.listeners=[];self.scene_listeners=[];self.writes=[]
    @property
    def selected_track(self):return self._track
    @selected_track.setter
    def selected_track(self,track):
        self._track=track;self.writes.append(track)
        if track.clip_slots:self.highlighted_clip_slot=track.clip_slots[self.song.scenes.index(self.selected_scene)]
        for callback in tuple(self.listeners):callback()
    def add_selected_track_listener(self,callback):self.listeners.append(callback)
    def remove_selected_track_listener(self,callback):self.listeners.remove(callback)
    def add_selected_scene_listener(self,callback):self.scene_listeners.append(callback)
    def remove_selected_scene_listener(self,callback):self.scene_listeners.remove(callback)

class PerformanceSong(BaseSong):
    def __init__(self,groups=2,compatible=True):
        self.track_reads=0;self.listeners=[]
        super().__init__(groups=groups,layers=1,scenes=4)
        self.file_path='test.als'
        for group in [t for t in self.tracks if t.is_foldable]:
            anchor=Track(self,'[Remote]',group,foldable=True)
            child=Track(self,'Technical',anchor)
            i=self.tracks.index(group)+2;self.tracks[i:i]=[anchor,child]
            track=self.tracks[i-1]
            self.routes(track,group,compatible)
        last=next(t for t in reversed(self.tracks) if t.group_track is not None and not t.group_track.name=='[Remote]')
        self.view=View(self,last)
    @property
    def tracks(self):self.track_reads+=1;return self._tracks
    @tracks.setter
    def tracks(self,value):self._tracks=value
    @staticmethod
    def routes(track,parent,compatible=False):
        track.available_input_routing_types=['Ext. In','No Input']
        track.available_input_routing_channels=['1 T-Bone (1)','2 SM58 (2)','1/2']
        track.output_routing_type=parent.name
        track.output_routing_channel=''
        track.current_monitoring_state=2
        if compatible:
            track.input_routing_type='Ext. In';track.input_routing_channel='1 T-Bone (1)'
    def add_tracks_listener(self,callback):self.listeners.append(callback)
    def remove_tracks_listener(self,callback):self.listeners.remove(callback)
    def create_audio_track(self,index):
        super().create_audio_track(index)
        track=self.tracks[index]
        parent=track.group_track
        self.routes(track,parent)
        self.view.selected_track=track
        for callback in tuple(self.listeners):callback()
