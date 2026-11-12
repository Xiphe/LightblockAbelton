# Idle discovery prototype

Question: can the Lightpad feel like a small, responsive, touchable space while
the Ableton controller is unavailable?

This is an independent throwaway Littlefoot program. It sends no musical or control MIDI,
read Live, or change the installed main controller or metronome. The bridge is
stopped during the device trial so there is only one SDK owner.

## Accepted device prototype: elastic depth, v9

Native curved shapes repeat through simulated depth. Color, changing size and
their offset centers create an elastic tunnel. No literal fractal computation
is required. Three patterns explore a single tunnel, intertwined loops and a
curved corridor.

- One finger bends the space directly.
- Pressure increases forward motion and distortion.
- Two fingers pull the near and far ends separately.
- Three simultaneous fingers change the pattern.
- Four return to the first pattern while motion continues.
- Touch exactly two opposite corners to preview the hidden message mode
  immediately. Either diagonal works. Hold for two seconds to keep it open;
  releasing sooner returns to the animation. After releasing both fingers,
  short-tap to cycle. The same preview/hold behavior closes the mode. Once a
  preview starts, both contacts may slide anywhere within the outer three-pixel
  edge band; entering the interior cancels an uncommitted preview.

V3 removed the bright touch/cursor markers. Hannes then clarified that slowing
all movement was not the desired effect: a new touch should join over about two
seconds, then follow movement directly. V4 captures the current end position at
touch-down and fades that finger's influence in with a two-second smoothstep.
Stable arrival-ordered roles prevent a new low-numbered sensor contact from
taking over an existing finger's end. Mature movement has only a short 8 ms
filter. Hannes accepted that behavior and requested the same easing on release.
V5 captures the released endpoint, pressure and twist, then fades their influence
back to the ongoing idle motion over two seconds. Re-touching during release
starts from the current visible state. Pattern changes also morph over two
seconds. Independent periodic waves avoid the former one-minute jump.

The whole-device Easter egg uses `idle-statements-prototype.littlefoot`.
The four screens are FCK/AFD, the six-stripe rainbow flag, the five-stripe trans
flag and white BLM lettering on black. FCK/AFD has solid red two-pixel stripes
at rows0..1 and13..14, with text at rows2..6 and8..12 and one blank middle row.
A four-by-five font fits three letters in14 columns. V8 incorporated the
immediate preview/hold-to-latch gesture and removed two withdrawn statements.
V9 permits continued edge movement instead of requiring exact corners throughout
the hold. The gesture is consumed once until every finger lifts. Moving into
the interior cancels the preview even between repaint calls;
release at the two-second threshold commits without waiting for a repaint.
Multi-touch and dragging inside message mode do not count as next-screen taps.
The animation's accepted two-second influence transitions remain in place.

BLM lettering was recommended for this first trial because it expresses the
requested solidarity directly. The red/black/green Pan-African flag has broader
African-diaspora significance; it is not interchangeable shorthand for BLM.
This is a recommendation about clarity, not a rule against white supporters
displaying the flag. Reference: [GSA flag history](https://www.gsa.gov/about-gsa/newsroom/former-gsa-regional-news-archive/region-8-newsroom/rocky-mountain-region-feature-stories-and-news-releases/a-historic-flag-raising-during-black-history-month-02152023).

With the bridge closed, run from the repository root:

```sh
blocksctl upload prototypes/controller/lightpad/idle-fractal-prototype.littlefoot --device YOUR_LIGHTPAD_SERIAL --timeout 30
```

The CLI stays running for the temporary trial. It does not replace the saved
startup program. Unplugging or ending the session can return to the old probe.
To save this accepted variant as the offline startup program, run:

```sh
blocksctl save-default prototypes/controller/lightpad/idle-fractal-prototype.littlefoot --device YOUR_LIGHTPAD_SERIAL --timeout 30
```

Keep the adjacent `idle-statements-prototype.littlefoot` include with the main
source. The save command completed on 2026-11-12. Hannes confirmed the animation
and opposite-corner gesture work with USB unplugged after a full power cycle.
The previous yellow feedback probe remains archived locally at
`verification/blocksctl-hardware-qa-2026-11-06/remaining-tests/baseline-with-heap.littlefoot`.
That private archive is not required to run this program.
Reopening the installed Lightpad Bridge app returns device ownership to the
normal controller after the prototype session has ended.

V9 compiles to 3,733 bytes plus 256 bytes heap, leaving 3,211 of 7,200 bytes free.
Sparse SDK diagnostic logs may travel over the connection. They encode `1000000 + callbackMilliseconds * 100 + pattern`.
Mode entry/exit logs are9871/9870; screen-cycle logs are9880..9883.
Preview logs9891/9890 mean statement/animation preview, and9892 cancels one.
V6 used3,580 bytes plus256 heap.
V8 used3,639 plus256.
V5 used 2,259 bytes plus 256 bytes heap.
V4 used 2,056 bytes plus 256 bytes heap. V3 used 1,716 plus 256; v2 used 1,542 plus 256.
Three USB runtime samples of v2 reported 9 ms for a complete scene; three v3
samples reported 12 ms. These are firmware-reported callback times, not touch latency or optical
frame-rate measurements. Compiled callback smoke checks cover all three pattern
branches, multiple fingers, reset, bounded geometry and absence of MIDI output.
The fake circle native records calls only; visual judgment happens on the pad.
Hannes loved v2's direction and response, then requested less cursor-like input,
softer transitions and removal of the brightest touch spot. V4's two-second
touch-admission behavior was accepted. The matching release and v9 hidden mode were accepted on the device. The final
edge-slide, thicker red bands and closer lettering received an LGTM on
2026-11-12. A compiled-bytecode regression reproduces the earlier edge-preview
cancellation and passes with v9, alongside nine gesture lifecycle checks.

## First attempt and direction change

V1 rendered a Julia escape-time fractal. The physical 3 ms slice managed only
one or two pixels per callback. Hannes found the general aesthetic interesting,
but requested much faster touch response and a stronger sense of curved 3D
space, using the display's limitations instead of treating it as a tiny image
renderer. V2 follows that direction. The later device trials accepted this direction. This capture is a standalone
prototype, ready to inform eventual controller-startup integration.

Local trial evidence and the previous Julia source are kept under the ignored
`verification/experiments/idle-fractal-prototype` directory. The program source
here is the runnable prototype; it does not depend on that evidence directory.
