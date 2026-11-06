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
