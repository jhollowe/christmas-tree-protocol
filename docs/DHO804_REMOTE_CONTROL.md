# Remote control of the RIGOL DHO804 over the network

How to drive the scope from a computer for this project: connect, configure, trigger, and pull waveform
data for decoding.

Source: *RIGOL DHO800/DHO900 Programming Guide* (PDF, 452 pages, 2023-08-29 build). Section numbers
below (`§3.28.5` etc.) refer to that document. Anything marked **[Check on first connect]** is *not*
stated in the guide and must be confirmed against the real instrument (see the checklist at the end).

> The guide PDF is not committed here. Put a local copy at `docs/reference/` (or keep it outside the
> repo). It is RIGOL's copyrighted document.

---

## 1. Connect

1. Plug the scope into the LAN with Ethernet (guide §2.2.2).
2. Configure addressing on the scope: notification area (bottom-right of screen) → **Utility** → **IO**.
   DHCP, auto-IP and manual addressing are all available (`:LAN:DHCP`, `:LAN:AUToip`, `:LAN:MANual`,
   `:LAN:IPADdress`, `:LAN:SMASk`, `:LAN:GATeway`; §3.14). A fixed address or a DHCP reservation is
   convenient for the lab computer.
3. Confirm the link: `:LAN:STATus?` returns one of `UNLINK, CONNECTED, INIT, IPCONFLICT, BUSY,
   CONFIGURED, DHCPFAILED, INVALIDIP, IPLOSE` (§3.14.10).

### Ways to talk to it

| Method | Use | Notes |
|---|---|---|
| **Web Control** | Quick manual poking | Browse to `http://<scope-ip>/`, click **SCPI Panel Control**, type a command, **Send & Read** (§2.2). Also shows the LXI page (model, serial, MAC, IP). |
| **Ultra Sigma** | RIGOL's PC tool | Auto-discovers LAN/USB instruments, has an SCPI panel (§2.2.2). Windows software from rigol.com. |
| **Raw TCP socket** | Our scripts, no drivers | SCPI text lines ending in `\n`. Needs the socket port, see below. |
| **PyVISA (+ `pyvisa-py`)** | Scripts that want a VISA API | Use the resource string the scope reports. |

The scope conforms to LXI Core 2011 (§2.2.2).

### Find the socket address and port

The guide does **not** print the raw-socket port number. Ask the instrument instead. In Web Control's SCPI
panel (no socket needed), send:

```
:LAN:VISA? SOCKet
```

`:LAN:VISA? [<type>]` accepts `USB`, `LXI` or `SOCKet` and returns a VISA address string (§3.14.11). The
guide does not show an example reply, so **[Check on first connect]** that the `SOCKet` reply contains the
host and TCP port (a VISA socket address normally looks like `TCPIP::<ip>::<port>::SOCKET`). Write it down in
`docs/lab-notes.md` (or wherever you keep lab-specific facts). Plain `:LAN:VISA?` returns the LXI address.

---

## 2. SCPI essentials (from the guide)

- Commands are case-insensitive. You may abbreviate to the capital letters: `:DISPlay:GBRightness?` →
  `:DISP:GBR?` (§2.1).
- Terminate every command with a newline (`\n`). Queries end in `?`.
- `*IDN?` identifies the instrument (§3.12.1). `*RST` resets (§3.12.2).
- **Error queue:** `:SYSTem:ERRor[:NEXT]?` pops one entry as `<number>,"<message>"`, e.g.
  `-113,"Undefined header; command cannot be found"` (§3.24.3). Poll it after a new sequence of commands.
  The guide does not show the "queue empty" reply; standard SCPI is `0,"No error"`, which is what the
  Python client below assumes **[Check on first connect]**.
- **Synchronization:** some commands are overlapped. `*OPC?` blocks until all prior commands have finished
  and then returns `1`. `*WAI` stops the instrument executing further commands until earlier ones complete
  (§2.3). Use `*OPC?` between configuration and capture.

### Run control (§3.1)

| Command | Effect |
|---|---|
| `:RUN` | Start acquiring |
| `:STOP` | Stop acquiring (required before reading RAW memory) |
| `:SINGle` | Arm one trigger, then stop (same as `:TRIGger:SWEep SINGle`) |
| `:TFORce` | Force a trigger (normal/single modes only) |
| `:CLEar` | Clear the screen |
| `:TRIGger:STATus?` | Returns `TD`, `WAIT`, `RUN`, `AUTO` or `STOP` (§3.27.3) |

---

## 3. Configure for this project

Typical starting point for the tree's data line (adjust once you see the real signal):

```
:CHANnel1:DISPlay ON
:CHANnel1:COUPling DC          # {AC|DC|GND}, default DC (§3.6.2)
:CHANnel1:SCALe 2              # V/div. DHO800 range at 1X probe: 500 µV/div to 10 V/div (§3.6.7)
:CHANnel1:OFFSet 0             # offset range depends on scale, e.g. ±20 V for 260 mV–2.65 V/div (§3.6.5)
:CHANnel1:PROBe 1              # set to match the physical probe (1, 10, ...) (§3.6.8)
:ACQuire:MDEPth 1M             # see memory-depth table
:TIMebase:MAIN:SCALe 0.0002    # 200 µs/div (example from guide §4.1)
:TRIGger:MODE EDGE
:TRIGger:EDGE:SOURce CHANnel1
:TRIGger:EDGE:SLOPe RFALl      # {POSitive|NEGative|RFALl} (§3.27.8.2)
:TRIGger:EDGE:LEVel -2.5       # see range note
:TRIGger:SWEep NORMal          # {AUTO|NORMal|SINGle} (§3.27.4)
:TRIGger:COUPling DC           # {AC|DC|LFReject|HFReject} (§3.27.2)
```

- **Trigger level range** for an analog channel is `(-4.5 × VerticalScale − OFFSet)` to
  `(4.5 × VerticalScale − OFFSet)` (§3.27.8.3). With zero offset, a trigger level near −5 V needs a vertical
  scale above about 1.1 V/div, so in practice 2 V/div; or shift the window with `:CHANnel1:OFFSet`.
  Mid-swing levels (e.g. −2.5 V) work at 1 V/div.
- **Trigger holdoff** (`:TRIGger:HOLDoff`, 8 ns to 10 s) is useful for locking onto the start of a repeating
  frame: set it longer than the frame but shorter than the idle gap (§3.27.5).
- **Pulse-width trigger** (`:TRIGger:MODE PULSe`, §3.27.9) can trigger on the long idle gap or reset between
  frames. Other trigger modes (RS232, IIC, SPI, NEDGe, TIMeout, DURation, …) exist in `:TRIGger:MODE`
  (§3.27.1) if the signal turns out to be serial.
- Built-in protocol decoders (`:BUS<n>` commands, §3.4) can decode UART/SPI/I²C on the scope. The scope's
  decode export is `:BUS<n>:EEXPort <path>` (§3.4.7).

### Memory depth

`:ACQuire:MDEPth {AUTO|1k|10k|100k|1M|10M|25M|…}` (§3.3.2). Available values on the DHO800 depend on how
many channels are enabled:

| Channels enabled | Available depths (DHO800) |
|---|---|
| 1 | AUTO, 1k, 10k, 100k, 1M, 10M, 25M |
| 2 | AUTO, 1k, 10k, 100k, 1M, 10M |
| 3 or 4 | AUTO, 1k, 10k, 100k, 1M |

Memory depth changes the sample rate. Read it back with `:ACQuire:SRATe?` (Sa/s). The capture window
is `depth / sample_rate`, so for long frames use one channel for the data line and a large depth.
For a single capture that must include the 29 V rail and the data line, remember that enabling 3+ channels caps
depth at 1M.

---

## 4. Reading waveform data (§3.28)

### Settings

| Command | Values |
|---|---|
| `:WAVeform:SOURce` | `CHANnel1`–`CHANnel4`, `MATH1`–`MATH4` (MATH only works in NORMal mode) |
| `:WAVeform:MODE` | `NORMal` screen data (1 to 1,000 points); `MAXimum` screen data in RUN, memory in STOP; `RAW` internal memory (STOP only) |
| `:WAVeform:FORMat` | `BYTE` (default, 1 byte/point), `WORD` (2 bytes/point), `ASCii` (volts, comma-separated) |
| `:WAVeform:STARt` / `:STOP` | First/last point to read, 1-based. RAW: 1 to current memory depth |
| `:WAVeform:POINts` | Number of points to read. RAW: 1 to current memory depth |
| `:WAVeform:PREamble?` | Ten comma-separated scaling values |

The guide notes you cannot operate the instrument while it is reading data.

### Procedure for a RAW (full-memory) read

From §3.28.5:

```
:STOP
:WAV:SOUR CHAN1
:WAV:MODE RAW
:WAV:FORM BYTE
:WAV:STAR 1
:WAV:STOP <memory depth>
:WAV:DATA?
```

Large reads may be done in batches by changing `STARt`/`STOP`; the guide says data in adjacent blocks is
consecutive and **each block carries its own TMC header** (§3.28 intro).

### Block format

For `BYTE`/`WORD`, the reply is `#N<N digits of length><length bytes of data><terminator>`. Example from the
guide: `#9000001000…` means 9 length digits, `000001000` = 1000 data bytes (§3.28 intro; the screenshot
command uses the same framing, §3.9.7).

### Preamble

`:WAVeform:PREamble?` returns (§3.28.14):

```
<format>,<type>,<points>,<count>,<xincrement>,<xorigin>,<xreference>,<yincrement>,<yorigin>,<yreference>
```

- `format`: 0 BYTE, 1 WORD, 2 ASC
- `type`: 0 NORMal, 1 MAXimum, 2 RAW
- `points`: 1 to 50,000,000
- `count`: averages count (1 outside averaging mode)

The guide's example reply: `0,0,1000,1,1.000000E-8,-5.000000E-6,0.000000E-12,4.000000E-03,0,128`.

Individual queries exist too: `:WAVeform:XINCrement?`, `XORigin?`, `XREFerence?`, `YINCrement?`, `YORigin?`,
`YREFerence?` (§3.28.6–3.28.11). In RAW mode `XINCrement = 1/SampleRate`; in NORMal mode it is
`TimeScale/100` (§3.28.6). `YINCrement` in NORMal mode is `VerticalScale/25` (§3.28.9).
**Re-query the preamble after any change to scale, offset, mode or format and before each read.**

### Conversion to volts and seconds

From the guide (§3.28): `volts = (raw − YORigin − YREFerence) × YINCrement`.
Time follows the same pattern using the X values: `t = (index − XREFerence) × XINCrement + XORigin`
(the reference/origin/increment definitions are in §3.28.6–3.28.8).

---

## 5. Python client (raw socket, no VISA)

Save as `tools/scope.py` when we create the tools directory. Standard library plus `numpy`.
Use the host and port from `:LAN:VISA? SOCKet`.

```python
import socket
import time

import numpy as np


class Scope:
    def __init__(self, host: str, port: int, timeout: float = 10.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.timeout = timeout

    def close(self) -> None:
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- low level -------------------------------------------------------
    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("scope closed the connection")
            buf += chunk
        return bytes(buf)

    def _drain(self) -> None:
        """Discard any bytes still pending (e.g. the block terminator)."""
        self.sock.settimeout(0.2)
        try:
            while self.sock.recv(4096):
                pass
        except socket.timeout:
            pass
        finally:
            self.sock.settimeout(self.timeout)

    # ---- SCPI ------------------------------------------------------------
    def write(self, cmd: str) -> None:
        self.sock.sendall(cmd.encode("ascii") + b"\n")

    def query(self, cmd: str) -> str:
        self.write(cmd)
        data = bytearray()
        while not data.endswith(b"\n"):
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("scope closed the connection")
            data += chunk
        return data.decode("ascii").strip()

    def query_block(self, cmd: str) -> bytes:
        """Send a query that returns an IEEE-488.2 block (#N<len><data>)."""
        self.write(cmd)
        if self._recv_exact(1) != b"#":
            raise ValueError("reply is not a TMC block")
        ndigits = int(self._recv_exact(1))
        length = int(self._recv_exact(ndigits))
        payload = self._recv_exact(length)
        self._drain()
        return payload

    def opc(self) -> None:
        self.query("*OPC?")

    def errors(self) -> list[str]:
        """Pop and return everything in the error queue."""
        out = []
        for _ in range(20):
            e = self.query(":SYSTem:ERRor?")
            if e.startswith("0,"):
                break
            out.append(e)
        return out

    # ---- capture ---------------------------------------------------------
    def capture_raw(self, channel: int = 1, chunk: int = 1_000_000):
        """Read the whole RAW memory of one channel. Scope must be stopped.

        Returns (time_seconds, volts) as numpy arrays.
        """
        depth = int(float(self.query(":ACQuire:MDEPth?")))
        self.write(f":WAVeform:SOURce CHANnel{channel}")
        self.write(":WAVeform:MODE RAW")
        self.write(":WAVeform:FORMat BYTE")
        self.opc()

        pre = self.query(":WAVeform:PREamble?").split(",")
        xinc, xorg, xref = float(pre[4]), float(pre[5]), float(pre[6])
        yinc, yorg, yref = float(pre[7]), float(pre[8]), float(pre[9])

        parts = []
        for start in range(1, depth + 1, chunk):
            stop = min(start + chunk - 1, depth)
            self.write(f":WAVeform:STARt {start}")
            self.write(f":WAVeform:STOP {stop}")
            parts.append(np.frombuffer(self.query_block(":WAVeform:DATA?"), dtype=np.uint8))
        raw = np.concatenate(parts).astype(np.float64)

        volts = (raw - yorg - yref) * yinc
        t = (np.arange(len(raw)) - xref) * xinc + xorg
        return t, volts

    def single_capture(self, wait_s: float = 30.0) -> None:
        """Arm a single trigger and wait until the scope reports STOP."""
        self.write(":SINGle")
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if self.query(":TRIGger:STATus?") == "STOP":
                return
            time.sleep(0.1)
        raise TimeoutError("no trigger within wait_s")

    def screenshot(self, path: str, fmt: str = "PNG") -> None:
        """Save the current screen. fmt is BMP, PNG or JPG."""
        with open(path, "wb") as f:
            f.write(self.query_block(f":DISPlay:DATA? {fmt}"))
```

Example session:

```python
with Scope("192.0.2.50", 5025) as s:     # use the host and port reported by :LAN:VISA? SOCKet
    print(s.query("*IDN?"))
    s.write(":CHANnel1:DISPlay ON")
    s.write(":TRIGger:SWEep NORMal")
    s.single_capture()
    t, v = s.capture_raw(channel=1)
    np.savez_compressed("captures/example.npz", t=t, v=v)
    print(s.errors())
```

`192.0.2.50` and `5025` are placeholders.

### PyVISA alternative

If you prefer a VISA API, pass the string returned by `:LAN:VISA?` (or `:LAN:VISA? SOCKet`) to
`pyvisa.ResourceManager("@py").open_resource(...)`, then use `query()`, `write()` and
`query_binary_values()` for the `:WAVeform:DATA?` block. Set `read_termination = "\n"`.

---

## 6. Practical notes for this project

- **Don't touch the scope while a block is being read** (guide: "You are not allowed to operate the
  instrument when it is reading data").
- **One capture = metadata + data.** Save the preamble, vertical scale, offset, probe ratio, memory depth,
  sample rate (`:ACQuire:SRATe?`), trigger settings, and which remote button / tree mode was active along with
  the arrays. The decoder in the plan needs them.
- **8-bit vs 12-bit:** `BYTE` has 256 levels across the 8-division screen. That is plenty for logic-level
  edges. Use `WORD` if you need analog detail (noise, slow ramps on the 29 V rail).
- **Screenshots** (`:DISPlay:DATA? PNG`) are handy to attach to lab notes next to each capture.
- **Measurements** without downloading data: `:MEASure:ITEM? <item>,CHANnel<n>` (e.g. `VPP`, `PDUTy`,
  `RTIMe`; §4.2) are useful for quick pulse-width and rise-time numbers.
- **Search** (`:SEARch` commands, §3.22) can find edges/pulses of a given width on the captured data.

---

## 7. First-connect checklist

Run these once on the real scope and record the results below. They cover items the guide does not
state, so the code above (BYTE only) doesn't depend on them.

- [ ] `:LAN:VISA? SOCKet` → record the exact address/port: `________`
- [ ] `*IDN?` returns a DHO804 identity string.
- [ ] `:LAN:VISA?` (LXI form) → record it in case PyVISA is wanted: `________`
- [ ] `WORD` format: byte order of each 16-bit sample (little vs big endian) and the value of `YREFerence`
      in WORD mode. Compare a `WORD` read against a `BYTE` read of the same stopped capture.
- [ ] Terminator after a block: 1 byte `\n`? (`_drain()` in the client handles either case.)
- [ ] Largest `:WAVeform:STOP − STARt` that works in one RAW read at each memory depth (the guide gives no
      per-read limit). Adjust `chunk` accordingly.
- [ ] Does `:ACQuire:MDEPth?` return a numeric value when set to `AUTO`? (The client expects a number;
      set an explicit depth before capturing.)
- [ ] Transfer speed for 1M and 25M points over your LAN.
- [ ] With the tree connected: confirm the scope input is not creating a ground loop (see the safety notes
      in `docs/PLAN.md`) before connecting probe grounds.
