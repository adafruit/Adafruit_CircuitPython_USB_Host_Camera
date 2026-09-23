# SPDX-FileCopyrightText: Copyright (c) 2026 Tim Cocks for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""
`adafruit_usb_host_camera`
================================================================================

CircuitPython USB host driver for UVC (USB Video Class) cameras.

Negotiates a video format with the camera, then reads whole frames. MJPEG
frames are complete JPEG images (see `UVCCamera.add_huffman_tables`) that can
be saved or decoded with ``jpegio``. YUY2 (uncompressed) frames are raw 4:2:2
pixels.

* Author(s): Tim Cocks

Implementation Notes
--------------------

**Hardware:**

* `Adafruit Fruit Jam <https://www.adafruit.com/product/6200>`_

**Software and Dependencies:**

* Adafruit CircuitPython firmware for the supported boards:
  https://circuitpython.org/downloads

"""

import struct
import time

import adafruit_usb_host_descriptors
import usb.core
from micropython import const

try:
    import displayio
    import jpegio
except ImportError:
    jpegio = None  # only UVCCamera.bitmap needs these

__version__ = "0.0.0+auto.0"
__repo__ = "https://github.com/adafruit/Adafruit_CircuitPython_USB_Host_Camera.git"

_DIR_IN = const(0x80)
_REQ_CLASS_IF_OUT = const(0x21)
_REQ_CLASS_IF_IN = const(0xA1)
_REQ_STD_IF_OUT = const(0x01)
_REQ_SET_INTERFACE = const(11)
_SET_CUR = const(0x01)
_GET_CUR = const(0x81)

_VS_PROBE_CONTROL = const(0x01)
_VS_COMMIT_CONTROL = const(0x02)

_CLASS_VIDEO = const(0x0E)
_SUBCLASS_VC = const(1)
_SUBCLASS_VS = const(2)
_CS_INTERFACE = const(0x24)
_VC_HEADER = const(0x01)
_VS_FORMAT_UNCOMPRESSED = const(0x04)
_VS_FRAME_UNCOMPRESSED = const(0x05)
_VS_FORMAT_MJPEG = const(0x06)
_VS_FRAME_MJPEG = const(0x07)

# Payload header bmHeaderInfo bits
_HDR_FID = const(0x01)
_HDR_EOF = const(0x02)
_HDR_ERR = const(0x40)

# Isochronous reads return records of a 16-bit length then that many bytes.
# This length marks packets the firmware could not keep.
_LOST = const(0xFFFF)

FORMAT_MJPEG = "MJPEG"
FORMAT_YUY2 = "YUY2"


class Mode:
    """One frame size and format the camera offers."""

    def __init__(self, format_type, format_index, frame_index, width, height, interval, max_size):
        self.format = format_type
        """``FORMAT_MJPEG``, ``FORMAT_YUY2``, or the raw character code"""
        self.format_index = format_index
        self.frame_index = frame_index
        self.width = width
        self.height = height
        self.interval = interval
        """Default frame interval in 100 ns units"""
        self.max_frame_size = max_size
        """Largest frame in bytes, from the frame descriptor"""

    def __repr__(self):
        return f"<Mode {self.format} {self.width}x{self.height}>"


def find_camera():
    """Return the first attached ``usb.core.Device`` that has a UVC streaming
    interface, or None. Other devices (keyboards, mice, hubs...) are skipped."""
    for device in usb.core.find(find_all=True):
        try:
            cfg = adafruit_usb_host_descriptors.get_configuration_descriptor(device, 0)
        except usb.core.USBError:
            continue  # could not read it, so not one we can drive
        i = 0
        while i + 6 < len(cfg) and cfg[i]:
            if (
                cfg[i + 1] == adafruit_usb_host_descriptors.DESC_INTERFACE
                and cfg[i + 5] == _CLASS_VIDEO
                and cfg[i + 6] == _SUBCLASS_VS
            ):
                return device
            i += cfg[i]
    return None


class UVCCamera:
    """A USB Video Class camera attached to the USB host port.

    :param device: a ``usb.core.Device`` for the camera. The default is the
        first camera `find_camera` sees, so other devices such as a keyboard
        can share the host ports.
    """

    def __init__(self, device=None):
        if device is None:
            device = find_camera()
            if device is None:
                raise ValueError("No UVC camera found")
        self.device = device
        self.modes = []
        """The `Mode` objects the camera offers"""
        self.dropped_frames = 0
        """Frames discarded because they were not valid JPEG data"""
        self.lost_packets = 0
        """Gaps in the stream: the firmware dropped its oldest packets because
        `capture` was not called for too long, or a packet arrived corrupted"""
        self.read_errors = 0
        """Reads that failed outright"""
        self.max_frame_size = 0
        self._vc_interface = None
        self._vs_interface = None
        self._uvc_version = 0x0100
        # (alternate setting, endpoint address, is_bulk, max packet size)
        self._endpoints = []
        self._parse(adafruit_usb_host_descriptors.get_configuration_descriptor(device, 0))
        if self._vs_interface is None or not self._endpoints:
            raise ValueError("No UVC streaming interface found")
        device.set_configuration()
        self._streaming = None
        self._ep = None
        self._bulk = False
        self._read_buf = None
        self._frame = None
        self._bitmap = None
        self._decoder = None

    def _parse(self, cfg):
        i = 0
        kind = None
        format_type = None
        format_index = 0
        alt = 0
        while i < len(cfg):
            length = cfg[i]
            if length == 0:
                break
            dtype = cfg[i + 1]
            if dtype == adafruit_usb_host_descriptors.DESC_INTERFACE:
                kind = None
                if cfg[i + 5] == _CLASS_VIDEO:
                    kind = cfg[i + 6]
                    if kind == _SUBCLASS_VC and self._vc_interface is None:
                        self._vc_interface = cfg[i + 2]
                    elif kind == _SUBCLASS_VS and self._vs_interface is None:
                        self._vs_interface = cfg[i + 2]
                    elif kind == _SUBCLASS_VS and cfg[i + 2] != self._vs_interface:
                        kind = None  # only drive the first streaming interface
                alt = cfg[i + 3]
            elif dtype == adafruit_usb_host_descriptors.DESC_ENDPOINT and kind == _SUBCLASS_VS:
                address = cfg[i + 2]
                if address & _DIR_IN:
                    mps = cfg[i + 4] | cfg[i + 5] << 8
                    size = (mps & 0x7FF) * (((mps >> 11) & 3) + 1)
                    self._endpoints.append((alt, address, cfg[i + 3] & 3 == 2, size))
            elif dtype == _CS_INTERFACE and kind == _SUBCLASS_VC:
                if cfg[i + 2] == _VC_HEADER:
                    self._uvc_version = cfg[i + 3] | cfg[i + 4] << 8
            elif dtype == _CS_INTERFACE and kind == _SUBCLASS_VS:
                sub = cfg[i + 2]
                if sub == _VS_FORMAT_MJPEG:
                    format_type = FORMAT_MJPEG
                    format_index = cfg[i + 3]
                elif sub == _VS_FORMAT_UNCOMPRESSED:
                    format_type = bytes(cfg[i + 5 : i + 9]).decode()
                    format_index = cfg[i + 3]
                elif sub in {_VS_FRAME_MJPEG, _VS_FRAME_UNCOMPRESSED}:
                    width, height = struct.unpack_from("<HH", cfg, i + 5)
                    max_size, interval = struct.unpack_from("<II", cfg, i + 17)
                    self.modes.append(
                        Mode(
                            format_type,
                            format_index,
                            cfg[i + 3],
                            width,
                            height,
                            interval,
                            max_size,
                        )
                    )
            i += length

    def find_mode(self, width=None, height=None, format_type=FORMAT_MJPEG):
        """Return the `Mode` matching ``format_type`` whose size is closest to
        ``width`` x ``height`` (smallest available when no size is given)."""
        best = None
        best_score = None
        for mode in self.modes:
            if mode.format != format_type:
                continue
            if width is None:
                score = mode.width * mode.height
            else:
                score = abs(mode.width - width) + abs(mode.height - height)
            if best is None or score < best_score:
                best = mode
                best_score = score
        return best

    def _probe(self, request, selector, data):
        bm = _REQ_CLASS_IF_IN if request & 0x80 else _REQ_CLASS_IF_OUT
        self.device.ctrl_transfer(bm, request, selector << 8, self._vs_interface, data)

    def start(self, mode, max_packet_size=None, frame_interval=None, read_size=8192):
        """Negotiate ``mode`` with the camera and start streaming.

        :param Mode mode: the format and frame size to stream
        :param int max_packet_size: largest isochronous packet to accept per
            1 ms USB frame, which limits the bandwidth the camera may use
        :param int frame_interval: frame interval in 100 ns units, overriding
            the mode's default. It must be one the camera supports, for
            example 2000000 for 5 fps.
        :param int read_size: bytes to request per isochronous read
        """
        size = 26 if self._uvc_version < 0x0110 else 34
        probe = bytearray(size)
        interval = frame_interval or mode.interval
        struct.pack_into("<HBBI", probe, 0, 1, mode.format_index, mode.frame_index, interval)
        self._probe(_SET_CUR, _VS_PROBE_CONTROL, probe)
        self._probe(_GET_CUR, _VS_PROBE_CONTROL, probe)
        self._probe(_SET_CUR, _VS_COMMIT_CONTROL, probe)
        negotiated, max_payload = struct.unpack_from("<II", probe, 18)
        self.max_frame_size = max(negotiated, mode.max_frame_size)

        # Bulk cameras stream on alternate setting 0. For isochronous ones use
        # the smallest allowed alternate setting whose packets hold the
        # camera's payload, or the largest allowed one if none do.
        choice = None
        for ep in self._endpoints:
            packet = ep[3]
            if ep[2]:
                choice = ep
                break
            if max_packet_size and packet > max_packet_size:
                continue
            if choice is None:
                choice = ep
            elif choice[3] < max_payload:
                if packet > choice[3]:
                    choice = ep
            elif max_payload <= packet < choice[3]:
                choice = ep
        if choice is None:
            raise ValueError("No streaming endpoint fits max_packet_size")
        alt, self._ep, self._bulk, packet = choice
        if not self._bulk or alt:
            self.device.ctrl_transfer(
                _REQ_STD_IF_OUT, _REQ_SET_INTERFACE, alt, self._vs_interface, None
            )
        # A bulk read ends at each payload boundary (short packet), so the
        # buffer must hold a whole payload. Isochronous reads return whole
        # records, so a read that leaves room for a largest record means the
        # firmware had nothing more buffered (see capture()).
        self._slack = packet + 2
        size = max_payload if self._bulk else max(read_size, 2 * self._slack)
        if self._read_buf is None or len(self._read_buf) != size:
            self._read_buf = bytearray(size)
        if self._frame is None or len(self._frame) < self.max_frame_size:
            self._frame = None
            self._frame = bytearray(self.max_frame_size)
        self._pos = 0
        self._count = 0
        self._records = False
        self._caught_up = False
        self._fid = None  # None until synchronized to a frame boundary
        self._reset_assembly(None)
        self._streaming = mode

    def _reset_assembly(self, buffer):
        self._out = buffer  # the buffer the state below refers to
        self._n = 0  # bytes of the frame being assembled
        self._base = 0  # where it starts in the frame buffer
        self._good = True
        self._done = 0  # length of the complete frame just before it

    def stop(self):
        """Stop streaming."""
        if self._streaming is None:
            return
        self.device.ctrl_transfer(_REQ_STD_IF_OUT, _REQ_SET_INTERFACE, 0, self._vs_interface, None)
        self._streaming = None

    def capture(self, buffer=None, timeout=2):
        """Return the newest complete frame.

        The firmware buffers about 200 ms of an isochronous stream. When the
        caller takes longer than a frame interval between calls, the frames
        that piled up meanwhile are skipped and the newest complete one is
        returned, so the caller never falls behind until that buffer
        overflows. Nothing counts the skipped frames; to get every frame,
        keep up with the camera or ask it for a lower rate with the
        ``frame_interval`` argument of `start`.

        :param bytearray buffer: optional buffer to fill; it must hold
            ``max_frame_size`` bytes and be the same object on every call.
            The default is an internal buffer that the next call overwrites.
        :param float timeout: seconds to wait for a whole frame
        :return: a memoryview of the frame data, at the start of the buffer
        """
        mode = self._streaming
        if mode is None:
            raise RuntimeError("call start() first")
        out = memoryview(buffer if buffer is not None else self._frame)
        if buffer is not self._out:
            self._reset_assembly(buffer)
            self._fid = None
        check_jpeg = mode.format == FORMAT_MJPEG
        buf = self._read_buf
        view = memoryview(buf)
        size = len(out)
        deadline = time.monotonic() + timeout
        # A complete frame occupies out[base - done:base] while the next one
        # assembles at out[base:base + n].
        n = self._n
        base = self._base
        good = self._good
        done = self._done
        while True:
            if self._pos >= self._count:
                if done and self._caught_up:
                    break
                if time.monotonic() > deadline:
                    self._n, self._base, self._good, self._done = n, base, good, done
                    raise RuntimeError("Timed out waiting for a frame")
                self._pos = self._count = 0
                try:
                    count = self.device.read(self._ep, buf, 100)
                except usb.core.USBTimeoutError:
                    continue
                except usb.core.USBError:
                    good = False  # data lost
                    self.read_errors += 1
                    continue
                if count < 2:
                    continue
                # A raw UVC payload starts with the header length and then
                # header flags with the end-of-header bit (0x80) set, so it never
                # looks like a record length.
                first = buf[0] | buf[1] << 8
                self._records = not self._bulk and (first < 0x8000 or first == _LOST)
                self._count = count
                self._caught_up = count + self._slack <= len(buf)

            pos = self._pos
            if self._records:
                length = buf[pos] | buf[pos + 1] << 8
                pos += 2
                if length == _LOST:
                    # Packets are missing here, so the frame in progress is
                    # unusable and what follows may be the tail of a later
                    # one. Resynchronize at the next end of frame.
                    self._pos = pos
                    self.lost_packets += 1
                    n = 0
                    good = True
                    self._fid = None
                    continue
                end = pos + length
            else:
                end = self._count
            if end - pos < 2 or buf[pos] > end - pos:
                self._pos = end
                continue
            header_len = buf[pos]
            info = buf[pos + 1]
            fid = info & _HDR_FID
            if self._fid is None:
                # Skip the rest of the frame in progress when streaming began.
                self._pos = end
                if info & _HDR_EOF:
                    self._fid = fid ^ 1
                continue
            if fid != self._fid:
                self._fid = fid
                if n:
                    # A new frame began without an EOF on the last one. Finish
                    # that frame and handle this payload on the next pass.
                    if good and self._complete(out, base, n, check_jpeg):
                        done = n
                        base += n
                        n = 0
                        good = True
                        # Only look for a newer frame when another one of
                        # this size fits behind it and reads return promptly.
                        if not self._records or 2 * done > size:
                            break
                        continue
                    n = 0
                    good = True
                    continue
            self._pos = end
            if info & _HDR_ERR:
                good = False
            data = end - pos - header_len
            if data > 0 and good:
                if base + n + data > size:
                    good = False
                else:
                    # Assigning into a slice of a big buffer moves everything
                    # after the slice (about 19 ms for a 150 KB frame buffer);
                    # filling a view of just the right size does not.
                    out[base + n : base + n + data][:] = view[pos + header_len : end]
                    n += data
            if info & _HDR_EOF:
                self._fid = fid ^ 1
                if good and self._complete(out, base, n, check_jpeg):
                    done = n
                    base += n
                    if not self._records or 2 * done > size:
                        n = 0
                        good = True
                        break
                n = 0
                good = True
        # Move the complete frame to the start of the buffer, and the frame in
        # progress right behind it.
        start = base - done
        if start:
            out[0:done][:] = out[start:base]
            if n:
                out[done : done + n][:] = out[base : base + n]
            base = done
        self._n, self._base, self._good, self._done = n, base, good, 0
        return out[:done]

    def _complete(self, out, base, n, check_jpeg):
        if n == 0:
            return False
        if check_jpeg and (n < 4 or out[base] != 0xFF or out[base + 1] != 0xD8):
            self.dropped_frames += 1
            return False
        return True

    @staticmethod
    def add_huffman_tables(frame):
        """Return an MJPEG frame as a standalone JPEG.

        MJPEG cameras usually leave out the Huffman tables, which most decoders
        and image viewers need. This inserts the standard ones when missing.

        :param frame: the frame from `capture`
        :return: ``bytes`` of a complete JPEG image
        """
        if _has_dht(frame):
            return bytes(frame)
        return b"\xff\xd8" + _DHT + bytes(frame[2:])

    def save_jpeg(self, path, frame=None):
        """Write an MJPEG frame to ``path`` as a standalone JPEG file.

        :param str path: the file to write
        :param frame: the frame from `capture`. The default captures a new
            one, which needs the camera to be streaming.
        """
        if frame is None:
            frame = self.capture()
        with open(path, "wb") as f:
            if _has_dht(frame):
                f.write(frame)
            else:
                f.write(b"\xff\xd8")
                f.write(_DHT)
                f.write(frame[2:])

    @property
    def bitmap(self):
        """A ``displayio.Bitmap`` of the camera image.

        The first access captures a frame and creates the bitmap at its size;
        later ones return the same bitmap unchanged. Call `update_bitmap` to
        show a new frame in it. Show it with a
        ``displayio.ColorConverter(input_colorspace=displayio.Colorspace.RGB565_SWAPPED)``
        pixel shader.
        """
        if self._bitmap is None:
            self.update_bitmap()
        return self._bitmap

    def update_bitmap(self):
        """Capture a frame with `capture` and decode it into `bitmap`,
        creating the bitmap the first time. A later, larger mode is shrunk by
        halves to fit.

        Needs an MJPEG mode and the ``jpegio`` module.

        :return: the `bitmap`
        """
        mode = self._streaming
        if mode is None:
            raise RuntimeError("call start() first")
        if mode.format != FORMAT_MJPEG:
            raise ValueError("bitmap needs an MJPEG mode")
        if jpegio is None:
            raise RuntimeError("bitmap needs jpegio")
        if self._decoder is None:
            self._decoder = jpegio.JpegDecoder()
        width, height = self._decoder.open(self.add_huffman_tables(self.capture()))
        bitmap = self._bitmap
        if bitmap is None:
            bitmap = self._bitmap = displayio.Bitmap(width, height, 65536)
        scale = 0
        while (width >> scale) > bitmap.width or (height >> scale) > bitmap.height:
            scale += 1
        self._decoder.decode(bitmap, scale=scale)
        return bitmap


# The standard Huffman tables that MJPEG streams leave out (ITU-T T.81 K.3),
# as one DHT segment.
_DHT = (
    b"\xff\xc4\x01\xa2"
    b"\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b"
    b"\x01\x00\x03\x01\x01\x01\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00"
    b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b"
    b"\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01\x7d"
    b"\x01\x02\x03\x00\x04\x11\x05\x12\x21\x31\x41\x06\x13\x51\x61\x07"
    b"\x22\x71\x14\x32\x81\x91\xa1\x08\x23\x42\xb1\xc1\x15\x52\xd1\xf0"
    b"\x24\x33\x62\x72\x82\x09\x0a\x16\x17\x18\x19\x1a\x25\x26\x27\x28"
    b"\x29\x2a\x34\x35\x36\x37\x38\x39\x3a\x43\x44\x45\x46\x47\x48\x49"
    b"\x4a\x53\x54\x55\x56\x57\x58\x59\x5a\x63\x64\x65\x66\x67\x68\x69"
    b"\x6a\x73\x74\x75\x76\x77\x78\x79\x7a\x83\x84\x85\x86\x87\x88\x89"
    b"\x8a\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7"
    b"\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5"
    b"\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2"
    b"\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8"
    b"\xf9\xfa"
    b"\x11\x00\x02\x01\x02\x04\x04\x03\x04\x07\x05\x04\x04\x00\x01\x02\x77"
    b"\x00\x01\x02\x03\x11\x04\x05\x21\x31\x06\x12\x41\x51\x07\x61\x71"
    b"\x13\x22\x32\x81\x08\x14\x42\x91\xa1\xb1\xc1\x09\x23\x33\x52\xf0"
    b"\x15\x62\x72\xd1\x0a\x16\x24\x34\xe1\x25\xf1\x17\x18\x19\x1a\x26"
    b"\x27\x28\x29\x2a\x35\x36\x37\x38\x39\x3a\x43\x44\x45\x46\x47\x48"
    b"\x49\x4a\x53\x54\x55\x56\x57\x58\x59\x5a\x63\x64\x65\x66\x67\x68"
    b"\x69\x6a\x73\x74\x75\x76\x77\x78\x79\x7a\x82\x83\x84\x85\x86\x87"
    b"\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5"
    b"\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3"
    b"\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda"
    b"\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf2\xf3\xf4\xf5\xf6\xf7\xf8"
    b"\xf9\xfa"
)


def _has_dht(frame):
    """Scan the JPEG marker segments before the scan data for a DHT."""
    i = 2
    n = len(frame)
    while i + 4 <= n and frame[i] == 0xFF:
        marker = frame[i + 1]
        if marker == 0xC4:
            return True
        if marker == 0xDA:
            return False
        i += 2 + (frame[i + 2] << 8 | frame[i + 3])
    return False
