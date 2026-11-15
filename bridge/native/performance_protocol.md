# Audio performance protocol

Private matched implementation block. The accepted UX is in lightpad/UX.md. Device owns contacts, gestures, mixer and recording view, local optimistic values and marker animation. Live owns actual musical facts. Bridge keeps latest received device intent separate from confirmed facts, coalesces parameter values and transports bounded commands. No Native objects leave Live callbacks. Retain the existing runtime as fallback; no installation until matched builds pass.

## Device intents

Channel1 CC frame, all data7-bit: `69 generation,70 sequence,71 key,72 low7,73 high7,74 matching-sequence`. Wrong generations are discarded before admission. Sequence is device-global modulo128. Entire frame must be valid before acting. Value is low7 | high7<<7. No raw contacts travel through Live. Parameter moves coalesce locally at40ms and always flush final value.

- key0..47: group*6 + control, absolute0..127; control0 volume,1 pan,2..5 the four assigned parameters.
- key56: toggle mute batch, value is8-bit group mask. One discrete transaction, all toggles at last release. Device previews its future mute state immediately. Ack value is actual muted group mask.
- key64..103:64+group*5+action; action0 Begin,1 Accept,2 Retry,3 Accept+Next,4 Cancel.
- Recording value low7 is current session token. Begin chooses a new token. Retry/Next value high7 is the next token, so a stale control cannot act on a replacement. Accept/Cancel high7 is zero. Token0 is legal.
- key112: Tap Tempo, discrete value0. key113: Click, absolute desired0/127. keys114/115: absolute numerator1..99 and denominator1/2/4/8/16. Their ACKs report actual values.
- Channel16 CC106 reports local held-contact count, CC107 reports local recording/pending-record UI busy, CC108 reports model-ready34main/35metronome; CC110 value19 requests fresh cached model after program initialization. No raw contact stream goes to Live.
- key104..111: Clear that group, value0. Stop its playing clips and advance to next free phrase row, preserving clips. This is discrete.

Parameter intents are replaceable per key. Discrete operations preserve order, deduplicate sequence+key+value within their bounded lifecycle, never replay an uncertain recording action. No automatic recording intent replay after reconnect. A full discrete queue fails explicitly; it never grows unbounded or silently drops a transaction. Gesture cancellation and view ownership stay local.

## Bridge/Live MIDI

Use the existing stable virtual ports, with explicit Native MIDI forwarding like OptimisticFaderSpike. Channel16 CC78 generation explicitly establishes a fresh bridge protocol session and is echoed by Live onCC79. It clears Native wire duplicate/observation sequence state and reseeds actual facts, preserving musical takes. This works even when a new helper process reuses a7-bit generation. Once warm, periodic CC79 is a keepalive that must not reset a healthy flight. Facts before the accepted session handshake are ignored. Each bridge command is channel1 `69 generation,70 wire-sequence,71 key,72 low7,73 high7,74 matching-wire-sequence`.

Live replies channel1 `69 generation,75 wire-sequence,76 key,77 low7,78 high7,79 matching-wire-sequence`. Parameter ACK value is actual value. Discrete accepted value0, except mute batch returns actual muted mask. Value16383 means rejected/failed. Retain the last completed wire command/reply to make duplicate frames idempotent. Reject mismatched generation/sequence/context. ACK is command admission/execution result, not proof recording has started.

One short command can be in flight. Prioritize discrete commands over pending parameter keys; coalesce each parameter key. A timeout reports uncertainty and does not retry a discrete mutation. Native mutation durations and command queue age are separately measurable. Preparation never runs inside receive_midi.

Live facts use a separate channel1 observation frame: `89 generation,90 observation-sequence,91 kind,92 a0,93 a1,94 a2,95 a3,96 b0,97 b1,98 b2,99 b3,100 matching-observation-sequence`, where a,b are unsigned28-bit values packed into four7-bit chunks. Malformed frames publish nothing. Kinds/payloads match the device messages below. Each observation is atomic. Emit changed facts, bounded clocks; do not send a whole-Set snapshot per touch. Discover/cache targets separately. Reconnect emits one bounded fresh model.

## Atomic bridge to device messages

SDK `handleMessage(a,b,header)`. Header low8=0xF2; bits8..10 kind0..7; bits11..17 generation1..127; bits18..30 model revision modulo8192. Every packet has a newer revision in the same generation; distance1..4095 is forward. Kind0 seeds a new generation. Other generations are ignored until seeded. Revision alone never permits overriding newer or active local intent.

- kind0 seed: a=last admitted device sequence,127 if none; b=0. A newly initialized program resumes its next sequence from a. An ongoing program must not rewind sequence on a same-generation reseed. Keep ongoing device gestures, safe desired parameter values, and resend the latest pending parameter intent after seed. Clear ambiguous discrete intent instead of replaying it.
- kind1 actual parameter: a=key0..47, b=actual0..127 or128 unavailable. Unavailable is visiblyzero and blocks new local drags; it is not reported as an actual minimum. The bridge gates Groupavailable until all six initial parameter facts, including128 sentinels, were sent, and resets that gate on reseed. Update confirmed fact; apply to visible value only without local ownership/outstanding newer intent.
- kind2 ACK: a=key | device-sequence<<7, b=actual/status. A matching latest ACK releases that intent. Stale ACK must not overwrite a newer intent. Rejection causes the established brief local failure feedback, without closing another take.
- kind3 group facts: a=group0..7; b bits0..20 RGB7 channels at0/7/14; bit21 available; bit22 populated; bit23 muted. Mute previews survive older facts until matching batch completion. Unassigned groups blank. Unavailable group gesture flashes locally.
- kind4 clock: a=group0..8 | span-in-quarter-beats*256<<4, b=position-in-quarter-beats*256. Group8 is the real global bar. Span and position max21bits; clamp actual display range, do not reinterpret a long loop as a one-bar loop. Global clock drives bar pulses and metronome. Live sends global roughly5Hz and visible refs at most2Hz; device interpolates with tempo.
- kind5 take facts: a=group bits0..2 | token bits3..9 | phase bits10..12 | captured-bar-length*16 bits13..25; b=measurement-in-quarter-beats*256. Phase0 idle,1 preparing,2 countdown,3 actually recording,4 pending finish,5 failed. Countdown measurement is remaining beats to actual scheduled target; recording/finish measurement is elapsed beats since actual start. Use actual start/meter metadata. Device calculates accepted8-bar dots/pages, fade and reverse countdown locally. Only matching group/token facts govern the current recording view. Pending finish keeps the view until actual completion, then overview; Next transitions to its replacement token without closing; its prepared start shares the old take's finish boundary. Preparing has no invented actual countdown target.
- kind6 global facts: a=0, b=tempo*128; a=1, b=numerator|denominator<<7|running<<14|click<<15. a=2 transport/health flags reserved. Tempo/meter reflect actual Live state.
- kind7 error: a=key | device-sequence<<7, b=bounded reason code. Same local brief failure treatment; no notifications for group errors.

Bridge assigns SDK revisions and maps wire ACK sequence to original device sequence. Delayed facts never sit ahead of performance command dispatch. Feedback queue coalesces latest fact by kind/context; discrete terminal results remain until sent. Model includes actual values and pending intent separately.

## Native workflow

Fixed test source: external mono T-Bone input1. Reuse a compatible quiet Audio track with empty current phrase slot, excluding current/following take, armed/playing/triggered/frozen tracks and technical descendants. Never copy an instrument/effect. A new take track is created just before an existing rightmost direct `[Remote]` subgroup, preserving prior tracks. Do not auto-migrate the heavy Set. Small fixture setup can create this agreed infrastructure explicitly.

Maintenance is a separate bounded callback workflow, one structural/routing operation at a time, with actual readiness validation. Prepare one next destination per group before launch and while recording. Do not scan the graph or create/route in the ready command path. Respect Native implicit parent routing and actual offered identities; labels alone do not distinguish sidechains. Preserve current selection after creation with the tested SelectionRestoration helper. Cooperative callbacks do not make a Native call asynchronous.

Recording follows existing UX timing: first accepted loop defines reference through actual leftmost playing loop; Begin/Retry wait for main-loop wrap when possible; finish at next safe bar, later bar if too close; Next accepts at a safe bar and starts its prepared replacement at that same boundary, without waiting for the base-loop wrap. A too-late unissued pair moves together to the following safe bar. No endless countdown on terminal preparation failure, no replay of ambiguously launched clips. Existing accepted clips/Set state must be preserved.

## Delivery gates

Portable host/protocol/actual compiled-bytecode tests, code+heap<=7200 and measured globals/stack budget, then one coherent cheap-Set physical trial. Only a new architectural/UX tradeoff requires user involvement before completion. Current production Set, bindings and devices remain untouched until matched block is ready. Metronome/status modes must remain available; do not silently remove controls to fit.

Live retains the existing channel16 health114..118 heartbeat every500ms and modal permission query109nonce/reply109allow or108deny. Program swaps reseed the same bridge generation and cached model without discarding Live command state. Actual device topology reconnection establishes a new generation and invalidates ambiguous discrete intent.

A forward gap in the SDK model revision requests one cached model reseed through device CC16/110/19. It does not close ongoing gestures or replay Native discrete commands. Stable state is not repeatedly streamed in full.
