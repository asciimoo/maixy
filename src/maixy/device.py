import collections
import contextlib
import struct
import threading
import time

KEEPALIVE_INTERVAL = 1.0
KEEPALIVE_TIMEOUT = 3.0


def image_packets(index, jpeg):
    """Port of MIT-licensed MXConsoleDefaultImageWriter (see THIRD-PARTY.txt)."""
    if len(jpeg) > 65535:
        raise ValueError('Key JPEG exceeds 65535 bytes')
    first = bytearray(4095)
    n = min(len(jpeg), 4075)
    first[:5] = bytes([0x14, 0xff, 0x02, 0x2b, 0xa1 | (0x40 if n == len(jpeg) else 0)])
    struct.pack_into('>HHHHHH', first, 5, 0x0100, 0x0100,
                     23 + (index % 3) * 158, 6 + (index // 3) * 158, 118, 118)
    struct.pack_into('>H', first, 18, len(jpeg))
    first[20:20 + n] = jpeg[:n]
    yield bytes(first)
    part = 2
    while n < len(jpeg):
        chunk = jpeg[n:n + 4090]
        n += len(chunk)
        packet = bytearray(4095)
        packet[:5] = bytes([0x14, 0xff, 0x02, 0x2b, part | 0x20 | (0x40 if n == len(jpeg) else 0)])
        packet[5:5 + len(chunk)] = chunk
        yield bytes(packet)
        part += 1


def pressed_keys(packet):
    if packet[:1] == b'\x13':
        d = packet[1:]
        if len(d) >= 6 and d[:3] == b'\xff\x02\x00' and d[4] == 1:
            keys = set()
            for b in d[5:]:
                if b == 0:
                    break
                if 1 <= b <= 9:
                    keys.add(b - 1)
            return keys
    return None


class Keypad:
    def __init__(self):
        import hid
        devices = hid.enumerate(0x046d, 0xc354)
        paths = {d['usage']: d['path'] for d in devices if d['usage_page'] == 0xff43}
        if not all(u in paths for u in (0x1a02, 0x1a08, 0x1a10)):
            raise RuntimeError('MX Keypad not connected (USB 046d:c354)')
        self.handles, self.by_report = {}, {}
        self._write_lock = threading.RLock()
        self._stop = threading.Event()
        self._redraw = threading.Event()
        self._worker = None
        self._worker_error = None
        self._last_heartbeat = None
        try:
            # macOS shares one path across collections; Windows does not.
            for report, usage in [(0x11, 0x1a02), (0x13, 0x1a08), (0x14, 0x1a10)]:
                path = paths[usage]
                if path not in self.handles:
                    h = hid.device()
                    h.open_path(path)
                    h.set_nonblocking(True)
                    self.handles[path] = h
                self.by_report[report] = self.handles[path]
            self.pending = collections.deque()
            self.keepalive_feature = self.feature(0x0008)
            self.brightness_feature = self.feature(0x8040)
            self.controls_feature = self.feature(0x1b04)
            for control in (0x01a1, 0x01a2):
                self.command(self.controls_feature, 0x3b, struct.pack('>HB', control, 3))
            self.heartbeat()
            # Process discovery, terminal inspection, and navigation may block
            # longer than the firmware deadline. Keep USB alive independently.
            self._worker = threading.Thread(target=self._keepalive, name='maixy-keepalive', daemon=True)
            self._worker.start()
        except Exception:
            self.close()
            raise

    def write(self, packet):
        with self._write_lock:
            n = self.by_report[packet[0]].write(packet)
            if n != len(packet):
                raise OSError('Short USB write: ' + str(n))

    def command(self, feature, function, payload=b''):
        if feature:
            packet = bytes([0x11, 0xff, feature, function]) + payload
            self.write(packet + bytes(20 - len(packet)))

    def read_all(self):
        result = []
        for h in self.handles.values():
            for _ in range(100):
                data = h.read(4096)
                if not data:
                    break
                result.append(bytes(data))
        return result

    def feature(self, number):
        self.command_root(number)
        deadline = time.monotonic() + .6
        while time.monotonic() < deadline:
            found = None
            for p in self.read_all():
                if len(p) >= 5 and p[:4] == b'\x11\xff\x00\x0b':
                    found = p[4]
                else:
                    self.pending.append(p)
            if found is not None:
                return found
            time.sleep(.01)
        raise RuntimeError('USB feature query timed out: ' + hex(number))

    def command_root(self, number):
        p = bytes([0x11, 0xff, 0, 0x0b]) + struct.pack('>H', number)
        self.write(p + bytes(20 - len(p)))

    def heartbeat(self):
        with self._write_lock:
            self.command(self.keepalive_feature, 0x1b, struct.pack('>H', int(KEEPALIVE_TIMEOUT * 1000)))
            self.command(self.brightness_feature, 0x2b, bytes([0, 80]))
            now = time.monotonic(), time.time()
            if self._last_heartbeat and max(now[i] - self._last_heartbeat[i] for i in (0, 1)) >= KEEPALIVE_TIMEOUT:
                self._redraw.set()
            self._last_heartbeat = now

    def _keepalive(self):
        while not self._stop.wait(KEEPALIVE_INTERVAL):
            try:
                self.heartbeat()
            except (OSError, RuntimeError) as error:
                self._worker_error = error
                return

    def needs_redraw(self):
        if self._worker_error is not None:
            raise self._worker_error
        with self._write_lock:
            # A timeout arriving during the repaint remains pending.
            if self._redraw.is_set():
                self._redraw.clear()
                return True
            return False

    def poll(self):
        if self._worker_error is not None:
            raise self._worker_error
        result = list(self.pending)
        self.pending.clear()
        return result + self.read_all()

    def paint(self, index, jpeg):
        with self._write_lock:
            for packet in image_packets(index, jpeg):
                self.write(packet)
            # Match the upstream writer's pacing to avoid dropped LCD draws.
            time.sleep(.01)

    def close(self):
        self._stop.set()
        if self._worker is not None:
            self._worker.join()
        for h in self.handles.values():
            with contextlib.suppress(Exception):
                h.close()
        self.handles.clear()
