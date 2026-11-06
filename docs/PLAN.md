# Plan of Attack: Reverse-engineer GE Microbright RGB+W tree → WLED

## Context
The goal is to control a GE 7.5' Grand Sonoma Fir (550 × 5mm "Microbright" RGB+W, Easy Light/Easy Shape, IR/RF remote) from WLED. The stock PSU/controller feeds the tree over a 3-pin connector:
- Pin A: "GND" (presumed)
- Pin B: +29 VDC
- Pin C: data, measured at **−5 VDC relative to "GND"**

Tools on hand: RIGOL DHO804 (4 ch, 12-bit, LAN/SCPI control), several ESP32s (logic analyzer / signal generator), and the repo `christmas-tree-protocol` (currently only LICENSE + README) to hold captures, scripts, docs, and eventually the usermod.

The repo is empty, so there's no existing code to reuse. Deliverables live in it: `docs/`, `captures/`, `tools/`, `usermod/`.

**Caveat on research done during planning:** a web search tool was available, but it only gives back synthesized summaries. It did not return primary sources I could check. It said GE RGB trees often use 29 V buck modules feeding standard 5 V WS281x pixels, and that WLED does not support GE Color Effects (G35/GECE) natively. **Treat both as unverified leads, not facts.** The −5 V data level you measured already doesn't match a plain WS281x-at-5V setup, so the protocol work has to rest on our own measurements.

---

## Phase 0 — Safety, limitations, ground rules
- **Scope grounding hazard:** the DHO804 probe grounds are tied to mains earth. Before connecting any probe ground, confirm the PSU output is isolated from earth: measure continuity/voltage from each output pin to the wall-plug earth pin with the PSU unplugged, then plugged in. If it is not isolated, use the scope only in differential mode (two channels, math A−B) or get an isolated/differential probe.
- **Do not trust the "GND" label yet.** Data at −5 V means one of these:
  1. The data line really swings negative relative to the PSU return (e.g. an inverting or bipolar driver).
  2. The pixel logic is referenced to some other rail: the "GND" pin is actually a +5 V logic rail and the real return is elsewhere, or the pixels are referenced high-side.
  3. What you measured is the *average* of a signal (a DMM reading DC on a switching waveform), not a static level.
  Phase 1 sorts out which.
- **ESP32 pins are 3.3 V and must never see a negative voltage.** Every ESP32 connection to the tree needs a level-translation front end (Phase 3). Never connect the 29 V rail to the ESP32.
- **Current budget:** 550 RGBW LEDs at full white could draw tens of watts. Read the PSU label (V/A) and write it down. Plan to keep using the stock 29 V PSU for the LEDs.
- **Keep the stock controller intact.** Don't cut the factory cable. Build a **pass-through breakout cable/adapter** (male↔female with test taps) so you can sniff in-line and later swap in the ESP32 without cutting anything. If the connector is proprietary, buy a spare extension cord or harvest a connector from it.

## Phase 1 — Passive electrical characterization (DMM + scope)
1. With the PSU disconnected from the tree, record no-load voltages on all pins against each other: A–B, A–C, B–C.
2. Repeat with the tree connected and lit, in several modes: off-by-remote, solid colors, solid white, animated.
3. Measure the DC voltage from each pin to earth, so you know the scope-safety situation from Phase 0.
4. Scope all three pins at once (CH1=A, CH2=B, CH3=C, probe ground on the confirmed PSU return). Use DC coupling and a long timebase (100 ms/div) to see if the data line is static or active:
   - Static −5 V while lit → the "data" may be a bias/reference rail, and the actual data may ride on the +29 V rail (power-line modulation, a known technique in cheap 2/3-wire strings). Zoom in on CH2 with AC coupling to look for modulation.
   - Active → go to Phase 2.
5. Check whether the 29 V rail stays steady or gets chopped/PWM'd, or even polarity-reversed. Many GE "two-mode" strings drive LEDs that way. If it does, the scheme may be "power rail modulation" rather than "addressable pixels".
6. Take photos of the PSU/controller label, FCC ID, and model numbers. **Look up the FCC ID on fccid.io.** Internal photos often show the controller MCU and driver chips, which tells you a lot without opening anything.

## Phase 2 — Protocol capture & decode (scope as primary instrument)
1. **Set up computer control of the DHO804 over LAN**: see [DHO804_REMOTE_CONTROL.md](DHO804_REMOTE_CONTROL.md) (connection, SCPI commands, waveform download, Python client, first-connect checklist). Write `tools/scope_capture.py` (built on the client in that page) to:
   - set channels, timebase, and trigger (edge or pulse-width on the data line);
   - pull waveform data (`:WAV:SOUR`, `:WAV:MODE RAW`, `:WAV:FORM BYTE/WORD`, `:WAV:DATA?`) and save it as `.npz`/CSV in `captures/<mode>_<timestamp>/` with metadata (mode, remote button, brightness).
   - **Done:** the DHO800/DHO900 Programming Guide was read; the command set is summarized in the remote-control page. Max memory depth on the DHO800 is 25M with one channel enabled, 10M with two, 1M with three or four (guide §3.3.2). The raw-socket port is not in the guide: get it from `:LAN:VISA? SOCKet` on the scope.
2. **First look:** with the trigger on any edge of the data line, describe the waveform:
   - idle level and active level (inverted? bipolar? 0/−5?);
   - bit timing: shortest pulse, period, whether there's a fixed bit cell (NRZ, PWM-coded like WS281x, Manchester, or UART-like start/stop bits);
   - frame structure: burst length, gap/reset time between frames, frame rate.
3. **Hypotheses to test, in order:**
   | Hypothesis | Signature |
   |---|---|
   | WS281x/SK6812-like (inverted and/or level-shifted) | ~1.25 µs bit cell, 2 pulse widths (~0.3/0.9 µs), >50 µs reset; 24 or 32 bits/pixel |
   | GE Color Effects (GECE/G35)-style | ~10 µs low / 20 µs high bit cells, 26-bit frames (6-bit addr, 8-bit brightness, 12-bit color), start/stop bits, slow |
   | UART/DMX-like broadcast | fixed baud, start/stop bits, bytes in packets |
   | Mode/"show" command only (pixels run their own patterns, the controller only picks a program) | short, rare bursts that only change when you press a remote button |
   | Power-line modulation on 29 V | data line static, 29 V rail has short dips/notches |
4. **Differential experiments** (one change at a time, capture before and after):
   - Remote: each button (on/off, mode, color, brightness, speed). Map button → waveform delta.
   - Solid red / green / blue / white at full and minimum brightness → locates the color bytes and their order, and shows whether W is a separate channel.
   - Count bits per frame and estimate the pixel count. 550 bulbs might be split into branch segments, or several bulbs could share one address. **Key question: are bulbs individually addressable, or grouped?** Check by watching which bulbs light during a "chase" mode, and by filming the tree in slow motion with a phone.
5. **Decoder:** write `tools/decode.py` to turn the captures into bit streams and then fields (address, brightness, R, G, B, W). Check it against the known solid-color captures.
6. **Long captures:** if the frames are longer than scope memory allows, use an ESP32 as a long-duration logic analyzer (Phase 3).

## Phase 3 — ESP32 front ends (sniffer + driver)
- **Input level shifting (sniffer):** to read a 0/−5 V (or bipolar) signal on a 3.3 V ESP32 input, use one of these:
  - an **op-amp or comparator** (LM393/TLV3201) referenced to the tree "GND", with the output pulled up to 3.3 V, plus a resistor divider and a clamp diode on the input; or
  - an **optocoupler** (6N137 / H11L1, both fast) driven from the data line through a resistor. This also gives isolation. Good choice if the grounds are tricky.
- **Logic-analyzer firmware:** use the ESP32 RMT peripheral in receive mode (≈12.5 ns resolution) to timestamp edges and stream them over USB serial or Wi-Fi to `tools/esp32_rmt_sniffer/`. Existing options to research: **ESP32 LogicAnalyzer (sigrok-compatible, e.g. "ESP32-LogicAnalyzer" / "logic_analyzer" by Ebiroll)**. Check that they're still maintained and that sigrok/PulseView compatibility works. A cheap **FX2-based 24 MHz USB logic analyzer (~$10, sigrok-supported)** is worth buying as a backup; it has a WS281x decoder built in.
- **Output driver (to drive the tree):** design this once you know the required levels. Candidates:
  - inverted 0/−5 V: a small negative rail (e.g. an ICL7660 charge pump or a −5 V tap from the tree side) plus a transistor/MOSFET level shifter, or
  - if the "GND" pin turns out to be a +5 V logic rail: a 74AHCT125 or NPN open-collector stage referenced correctly.
  Build it on perfboard and test it on **one branch section** first (Phase 4).

## Phase 4 — Replay & synthetic signal tests
1. **Replay:** play a captured frame back with the ESP32 (RMT TX) into the tree through the output driver, with the stock controller disconnected. Success = the tree shows the same state as when you captured it.
2. **Synthesis:** send self-built frames: all red, single bulb #0 lit, a walking pixel. Confirm the addressing model, the color order, the W channel, brightness behavior, and the maximum frame rate.
3. Characterize the timing tolerances (how much the bit timing can stretch or shrink) so you know whether the RMT, I2S, or SPI peripheral will work for output.
4. Check whether the pixels need any startup/enumeration sequence after power-on (GECE does). Capture the first few hundred ms after power-on.

## Phase 5 — Decide the WLED integration path
Steps to take:
1. **Check existing WLED support first:** look at the WLED bus types in `wled00/const.h` (`TYPE_*`) and `bus_wrapper.h`, plus the NeoPixelBus features list. If the protocol turns out to be WS281x/SK6812 with just inverted or shifted levels, **no usermod is needed**: use stock WLED with SK6812 RGBW (or WS281x) and do the level conversion in hardware. Also ask on the WLED Discord/forum/GitHub issues and search r/WLED and r/christmaslights for this GE model. (These are research items; I couldn't check WLED source or forums during planning.)
2. **If the protocol isn't supported:** choose between these:
   - **Option A (recommended for a non-standard protocol): WLED usermod with a custom output.** The usermod reads WLED's pixel buffer every frame (via `strip.getPixelColor(i)` in `handleOverlayDraw()` or `loop()`, with WLED's own bus set to a dummy/virtual output) and sends it on a GPIO through RMT using a custom encoder. Check the current WLED 0.15/0.16 usermod API (`Usermod` class, `setup/loop/handleOverlayDraw/addToConfig/readFromConfig`, `usermods_list.cpp` vs. the newer library-style `usermods/<name>/library.json` registration). The API has changed recently, so confirm against the WLED version you build.
   - **Option B: bridge.** Stock WLED sends DDP/E1.31 over the network (virtual bus) to a second ESP32 running small custom firmware that speaks the tree protocol. This isolates the protocol code from WLED, which makes it the quickest way to a working tree and a fallback if Option A is hard to get working.
   - **Option C: if the tree only accepts "show mode" commands** (not addressable): a usermod that maps WLED presets/effects to tree program commands. This is a much more limited result; document it as a constraint.
3. **Pixel mapping:** use a `ledmap.json` for the physical tree layout. Also look at 2D/3D mapping for effects that look like a tree (a phone camera with xLights' "Model Generator" to map bulb positions).

## Phase 6 — Usermod implementation (if needed)
- Location: `usermod/usermod_ge_microbright/` in this repo, symlinked/copied into a WLED source checkout for building with PlatformIO.
- Parts:
  - `ge_microbright.h/.cpp`: the Usermod subclass; config for GPIO, pixel count, color order, W handling, and a grouping factor.
  - Encoder: RMT TX with a pre-built item table (bit → high/low durations); DMA/non-blocking so WLED's Wi-Fi isn't starved.
  - Color conversion: WLED 24/32-bit → tree format (handle bit depth reduction and gamma; add an RGB→RGBW white extraction if WLED isn't already producing W).
  - Frame-rate limiting that respects the reset time measured in Phase 4.
- Build: PlatformIO env `esp32dev` with `custom_usermods = ge_microbright` (or the equivalent for the WLED version you use).
- Hardware: ESP32 + output driver PCB + a 29 V→5 V buck to power the ESP32 from the tree PSU (rated for 29 V+ input with margin, e.g. a 36 V+ rated buck module).

## Hardware shopping list
- 3-pin connector set or spare extension cable for the breakout/pass-through adapter
- Cheap 24 MHz USB logic analyzer (sigrok/PulseView)
- Optocouplers 6N137 / H11L1, comparators LM393/TLV3201, 74AHCT125, small-signal NPN/PNP and N/P-MOSFETs (2N7002, BSS84)
- ICL7660/TC7660 charge pump (for a −5 V rail if needed)
- Buck converter rated ≥36 V input, 5 V out
- Optional: differential/isolated scope probe if the PSU isn't isolated
- Phone with slow-motion video for addressing/chase analysis

## Research checklist (still to do)
- [ ] FCC ID lookup of the controller/remote (internal photos, chip IDs, RF vs IR remote)
- [ ] Search for teardowns: "GE Microbright", "GE Easy Light RGB", "GE Energy Smart Colorite" (*Colorite* is GE's older color-changing line, so it's a lead worth checking), r/WLED, r/christmaslights, DIYC forums, Hackaday
- [ ] Datasheets for any chips found on the controller PCB or inside a bulb/pixel (if one can be opened safely)
- [x] Rigol DHO800 programming guide (SCPI commands and memory depth done, see DHO804_REMOTE_CONTROL.md). Still to try: the scope's built-in `:BUS<n>` decoders (guide §3.4) on the tree's data line, which may shortcut the decode step if the signal is serial.
- [ ] WLED: current bus types and usermod API for the version you'll target
- [ ] ESP32 sigrok logic-analyzer firmware status
- [ ] ESPixelStick: if the protocol turns out to be GECE-like, its GECE output code is a good reference implementation

## Verification (end-to-end)
1. Decoder output matches known remote-selected states (solid colors, brightness) for all captured modes.
2. ESP32 replay and synthetic frames produce exactly the expected bulbs and colors, including single-pixel addressing.
3. WLED (stock or with the usermod) drives the tree: solid colors match, effects run smoothly at the target frame rate, no flicker over a 1-hour soak test, and the output driver and buck stay cool.
4. Commit captures, decoder, docs (`docs/protocol.md` with timing diagrams), and the usermod to this repo.
