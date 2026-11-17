# MIDI routing verdict

Native trial on 2026-11-18 with a stock Drift instrument and a small saved Set.
The production Audio controller and Temple of Loops were not changed by this
spike. Temple was saved before switching Sets.

## Established behavior

- A mixed Audio/MIDI pair started at beat32 and its following pair at beat44.
  Both first clips were12 beats long; each MIDI clip contained six actual notes.
- An original outside instrument can feed one shared No Input Audio receiver
  inside R1. The receiver also works nested under the rightmost `[Remote]`
  subgroup. No instrument or source effects are copied.
- A Post Mixer tap of R1 carried the signal normally and after restoration.
  Its settled meter reached zero for group mute and volume zero while the
  instrument still produced signal. Native meters have a decay envelope; this
  is a routing check, not a latency measurement.
- The original instrument can provide the sole live MIDI monitor while armed
  MIDI capture lanes use Monitor Off and target its direct instrument endpoint.
  A sounding recorded layer produced zero notes in a second take without new
  external input. That take subsequently contained only the newly played
  pitch67, excluding the old layer's pitch65.
- A timestamped mixed pair recorded pitch69 and a following pair pitch74.
  MIDI/Audio lengths matched at36 and44 beats. Signal was present in sampled
  observations before and after Next while CC64 was held. The saved Set contains
  the injected pedal envelopes. This does not prove audible pedal continuity or
  correct pedal replay across independently looping layers.

## Proposed next architecture, awaiting user agreement

The tested Return offered Audio-track destinations but no direct R-group input.
Use one fixed shared Audio receiver per R-group under `[Remote]`, with instruments
routed directly to that receiver. Eight groups would need eight receivers,
independent of the instrument count, and no additional global routing Returns.
Instrument ownership remains exclusive to one group until Clear, as agreed.

This spike has not implemented ownership, selection persistence, the25-source
protocol, mixed production reserves or controller integration. Physical
responsiveness, MPE, plugin-specific sustain and MIDI-effect/rack entry points
still need targeted checks. The input selector's agreed UX remains unchanged.

## Provenance

Actual probe outputs are retained privately in experiment85. The small public
[evidence summary](evidence.json) records the relevant anchors, notes and mixer
samples without the Set or media. Earlier experiment54 established direct
instrument addressing; this spike adds the shared receiver and input isolation.
The direct instrument target, rather than Track In, follows Live's documented
[multiple-track instrument routing](https://www.ableton.com/en/live-manual/12/routing-and-i-o/#several-midi-tracks-playing-the-same-instrument).
Monitor Off capture follows the documented separation between
[monitoring and recording](https://www.ableton.com/en/live-manual/12/recording-new-clips/#choosing-an-input).
