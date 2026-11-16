"""One logical Audio take over preallocated, individually owned destinations.

Native launch requests are consecutive, never atomic. Partial/uncertain launch
is contained without replay, and only the new transaction's owned audio may be
deleted. The previous handoff take is always preserved.
"""
from .recording_core import Take, EPSILON, LEAD, FIRE_LEAD
from .launch_admission import LaunchAdmission
from .model import read_grid, same
from .audio_inputs import Destination


def members(take):
    return (take,)+tuple(getattr(take,'members',()))


class BundleRecording:
    """Mixin preceding PreparedRecording; the legacy path does not use it."""
    def reconcile(self):
        previous=self.take
        super().reconcile()
        if previous is not None and previous is not self.take and previous.state=='completed':
            for item in members(previous)[1:]:
                if item.armed_here and not any(item is queued for queued in self._disarms):
                    self._disarms.append(item)

    def _attach(self,take):
        destinations=self._allocated_members
        self._allocated_members=()
        take.source=destinations[0].source
        take.members=tuple(Take(take.group,take.parent,m.track,m.slot,take.row,take.token,
                                take.reference,queued_at=take.queued_at,target=take.target,
                                handoff_target=take.handoff_target) for m in destinations[1:])
        for child,destination in zip(take.members,destinations[1:]):child.source=destination.source
        return take

    def _allocate(self,group,row,exclude=(),reuse=None):
        item=self._group(group);reserve=self.reserves.get(group)
        if reserve is None or not self.inputs.validate(reserve,group,row):
            raise ValueError('prepared Audio bundle unavailable')
        if any(any(same(m.track,t) for t in exclude) for m in reserve.members):
            raise ValueError('prepared Audio bundle overlaps current take')
        self._allocated_members=tuple(Destination(self.inputs.sources[m.source.index],m.track,m.slot)
                                      for m in reserve.members)
        self.reserves.pop(group,None)
        return item.group,reserve.track,reserve.slot

    def _make(self,*args,**kwargs):return self._attach(super()._make(*args,**kwargs))

    def accept(self,group,next_take=False):
        take=self.take
        result=super().accept(group,next_take)
        if next_take and take is not None and take.following is not None:
            self._attach(take.following)
        if take is not None and take.state=='finish_queued':
            for child in getattr(take,'members',()):
                child.finish_target=take.finish_target;child.state='finish_queued'
        return result

    def _identity(self,take,empty=False):
        result=None
        for item in members(take):result=super()._identity(item,empty)
        return result

    def _observe(self,take,effects=False):
        if not getattr(take,'members',()):
            super()._observe(take,effects)
            self._stamp(take)
            return
        previous=take.state
        observed=[]
        for item in members(take):
            # The aggregate state lives on the primary member. Each member
            # still retains its own issued/clip/cleanup ownership facts.
            if item is take:item.state=previous
            super()._observe(item,effects)
            self._stamp(item);observed.append(item.state)
        anchors=[m.anchor for m in members(take) if m.anchor is not None]
        if anchors and any(abs(a-anchors[0])>EPSILON for a in anchors):
            self._contain(take,ValueError('Audio bundle recording anchors diverged'),
                          preserve=take.finish_target is not None)
            return
        if all(s=='completed' for s in observed):take.state='completed'
        elif all(s in ('recording','finish_queued') for s in observed):
            take.state='finish_queued' if previous=='finish_queued' else 'recording'
        elif previous not in ('canceling','completed'):take.state=previous
        if previous=='recording' and 'completed' in observed and any(s=='recording' for s in observed):
            self._contain(take,ValueError('Audio bundle member was stopped externally'),preserve=True)

    def _stamp(self,take):
        source=getattr(take,'source',None)
        if source is not None and take.clip is not None and not getattr(take,'source_stamped',False):
            take.source_stamped=True
            try:take.clip.name=source.name;take.clip.color=source.color
            except (RuntimeError,ValueError,AttributeError) as error:
                self._event('source_clip_appearance_unavailable',take,reason=str(error))

    def _defer(self,take,finish=False,admission=None):
        super()._defer(take,finish,admission)
        for item in members(take)[1:]:
            item.target=take.target
            item.finish_target=take.finish_target
        if take.following is not None:
            for item in members(take.following)[1:]:
                item.target=take.following.target;item.handoff_target=take.following.handoff_target

    def _arm_members(self,take):
        for item in members(take):
            if item.track.is_frozen or not self._quiet(item.track):raise ValueError('Audio destination is not quiet')
            if not item.armed_here:
                if item.track.arm:raise ValueError('Audio destination armed externally')
                item.armed_here=True;item.track.arm=True
        if any(not item.track.arm for item in members(take)):
            raise ValueError('Live did not retain all Audio recording arms')

    def _unarm_unissued(self,take):
        for item in members(take):
            if item.armed_here and not item.start_issued:
                item.track.arm=False;item.armed_here=False

    def _execute(self,take,finish=False):
        if not self.song.is_playing or (take.finish_issued if finish else take.start_issued):return
        for unused in range(3):
            grid=read_grid(self.song);admission=LaunchAdmission(self.song,clock=self.monotonic)
            target=take.finish_target if finish else take.target
            previous,quantum=self._window(target,grid)
            if grid.position<=previous+EPSILON:return
            try:admission.require(target,LEAD)
            except ValueError:self._defer(take,finish,admission);continue
            self._identity(take,empty=not finish)
            if finish:
                if any(m.clip is None or not m.clip.is_recording for m in members(take)):return
            else:
                try:self._arm_members(take)
                except (RuntimeError,ValueError,AttributeError) as error:
                    self._contain(take,error);return
            self._identity(take,empty=not finish)
            try:admission.require(target,FIRE_LEAD)
            except ValueError:
                if not finish:self._unarm_unissued(take)
                self._defer(take,finish,admission);continue
            try:
                if not finish:take.bundle_start_at=self.monotonic()
                else:
                    take.bundle_finish_at=self.monotonic()
                    take.bundle_finish_target=take.finish_target
                for item in members(take):
                    if finish:item.finish_issued=True
                    else:item.start_issued=True
                    item.slot.fire(launch_quantization=quantum)
                    self._event('finish_issued' if finish else 'start_issued',item)
            except Exception as error:
                self._contain(take,error,preserve=finish)
            return

    def _execute_pair(self,take):
        following=take.following
        if (following is None or following.state=='canceling' or take.finish_issued
            or following.start_issued or not self.song.is_playing):return
        for unused in range(3):
            grid=read_grid(self.song);admission=LaunchAdmission(self.song,clock=self.monotonic)
            target=take.finish_target
            if following.target!=target:raise ValueError('handoff targets diverged')
            previous,quantum=self._window(target,grid)
            if grid.position<=previous+EPSILON:return
            try:admission.require(target,LEAD)
            except ValueError:self._defer(take,True,admission);continue
            self._identity(take);self._identity(following,empty=True)
            if any(m.clip is None or not m.clip.is_recording for m in members(take)):return
            try:self._arm_members(following)
            except (RuntimeError,ValueError,AttributeError) as error:
                self._contain(take,error,preserve=True);return
            self._identity(take);self._identity(following,empty=True)
            try:admission.require(target,LEAD)
            except ValueError:
                self._unarm_unissued(following);self._defer(take,True,admission);continue
            try:
                following.bundle_start_at=self.monotonic()
                take.bundle_finish_at=self.monotonic()
                take.bundle_finish_target=take.finish_target
                for item in members(take):
                    item.finish_issued=True;item.slot.fire(launch_quantization=quantum)
                for item in members(following):
                    item.start_issued=True;item.slot.fire(launch_quantization=quantum)
                self._event('finish_issued',take,next_token=following.take_id,next_target=following.target)
                self._event('start_issued',following,previous_token=take.take_id)
            except Exception as error:
                self._contain(take,error,preserve=True)
            return

    def _contain(self,take,error,preserve=False):
        self._bundle_cleanup=dict(take=take,following=take.following,preserve=preserve,
                                  reason=str(error),stopped=set(),
                                  token=take.following.take_id if take.following is not None else take.take_id)
        take.failure=str(error);take.state='bundle_cleanup'
        self._event('bundle_enqueue_uncertain',take,reason=str(error))

    def _cancel_one(self,take):
        done=True
        for item in members(take):done=super()._cancel_one(item) and done
        return done

    def acknowledge(self,group,take_id):
        take=self.take
        if take is not None and take.state=='completed' and take.group==group and take.take_id==take_id:
            for item in members(take)[1:]:
                if item.armed_here:self._disarms.append(item)
        return super().acknowledge(group,take_id)

    def tick(self):
        cleanup=getattr(self,'_bundle_cleanup',None)
        if cleanup is not None:
            take=cleanup['take']
            try:
                done=True
                if cleanup['preserve']:
                    for item in members(take):
                        super()._identity(item)
                        if id(item) not in cleanup['stopped']:
                            cleanup['stopped'].add(id(item));item.track.stop_all_clips(False)
                        if not self._quiet(item.track):done=False
                        elif item.armed_here:item.track.arm=False;item.armed_here=False
                else:done=self._cancel_one(take) and done
                if cleanup['following'] is not None:
                    done=self._cancel_one(cleanup['following']) and done
                if done:
                    self.failed_bundle=(take.group,cleanup['token'],cleanup['reason'])
                    self.take=None;self._bundle_cleanup=None
            except (RuntimeError,ValueError,AttributeError) as error:
                self.issues[take.group]=str(error)
                # Never repeat an uncertain stop/delete automatically.
                self.failed_bundle=(take.group,cleanup['token'],str(error))
                self.take=None;self._bundle_cleanup=None
            return self._drain()
        result=super().tick()
        take=self.take
        if take is not None and take.state=='queued' and take.start_issued:
            if (self.song.current_song_time>take.target+read_grid(self.song).length
                or self.monotonic()-getattr(take,'bundle_start_at',self.monotonic())>8):
                self._contain(take,ValueError('Audio bundle start was not confirmed'))
        if take is not None and take.state=='finish_queued' and getattr(take,'members',()):
            recording=[m.clip is not None and m.clip.is_recording for m in members(take)]
            target=getattr(take,'bundle_finish_target',None)
            expired=(target is not None and self.song.current_song_time>target+read_grid(self.song).length)
            expired=expired or self.monotonic()-getattr(take,'bundle_finish_at',self.monotonic())>8
            if any(recording) and expired:
                self._contain(take,ValueError('Audio bundle finish was not confirmed'),preserve=True)
        if take is not None and take.state=='completed':
            for item in members(take)[1:]:
                if item.armed_here and item.clip is not None and not item.clip.is_recording:
                    item.track.arm=False;item.armed_here=False
        return result
