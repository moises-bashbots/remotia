"""macOS audio playback via AudioQueue (ctypes).

Uses the system AudioToolbox framework so no extra bundled libraries are
needed. Accepts raw PCM / float audio and plays it with a simple ring of
AudioQueue buffers.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import CFUNCTYPE, POINTER, Structure, byref, c_double, c_uint32, c_void_p

_AUDIO_TOOLBOX = "/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox"
_at = ctypes.cdll.LoadLibrary(_AUDIO_TOOLBOX)

kAudioFormatLinearPCM = 0x6C70636D  # 'lpcm'
kLinearPCMFormatFlagIsFloat = 0x1
kLinearPCMFormatFlagIsSignedInteger = 0x4
kLinearPCMFormatFlagIsPacked = 0x8


class AudioStreamBasicDescription(Structure):
    _fields_ = [
        ("mSampleRate", c_double),
        ("mFormatID", c_uint32),
        ("mFormatFlags", c_uint32),
        ("mBytesPerPacket", c_uint32),
        ("mFramesPerPacket", c_uint32),
        ("mBytesPerFrame", c_uint32),
        ("mChannelsPerFrame", c_uint32),
        ("mBitsPerChannel", c_uint32),
        ("mReserved", c_uint32),
    ]


class AudioQueueBuffer(Structure):
    _fields_ = [
        ("mAudioDataBytesCapacity", c_uint32),
        ("mAudioData", c_void_p),
        ("mAudioDataByteSize", c_uint32),
        ("mUserData", c_void_p),
    ]


CALLBACK = CFUNCTYPE(None, c_void_p, c_void_p, c_void_p)

_at.AudioQueueNewOutput.argtypes = [
    POINTER(AudioStreamBasicDescription), CALLBACK, c_void_p, c_void_p, c_void_p, c_uint32,
    POINTER(c_void_p),
]
_at.AudioQueueNewOutput.restype = c_uint32
_at.AudioQueueAllocateBuffer.argtypes = [c_void_p, c_uint32, POINTER(c_void_p)]
_at.AudioQueueAllocateBuffer.restype = c_uint32
_at.AudioQueueEnqueueBuffer.argtypes = [c_void_p, c_void_p, c_uint32, c_void_p]
_at.AudioQueueEnqueueBuffer.restype = c_uint32
_at.AudioQueueStart.argtypes = [c_void_p, c_void_p]
_at.AudioQueueStart.restype = c_uint32
_at.AudioQueueStop.argtypes = [c_void_p, ctypes.c_ubyte]
_at.AudioQueueStop.restype = c_uint32
_at.AudioQueueDispose.argtypes = [c_void_p, ctypes.c_ubyte]
_at.AudioQueueDispose.restype = c_uint32


class AudioQueuePlayer:
    """Plays PCM audio pushed via :meth:`write`."""

    def __init__(self, sample_rate: int, channels: int, bits: int, is_float: bool) -> None:
        self._lock = threading.Lock()
        self._data = bytearray()
        self._queue = None
        self._buffers: list[int] = []

        bytes_per_frame = (bits // 8) * channels
        flags = kLinearPCMFormatFlagIsPacked
        if is_float:
            flags |= kLinearPCMFormatFlagIsFloat
        elif bits > 8:
            flags |= kLinearPCMFormatFlagIsSignedInteger

        fmt = AudioStreamBasicDescription(
            float(sample_rate), kAudioFormatLinearPCM, flags,
            bytes_per_frame, 1, bytes_per_frame, channels, bits, 0,
        )

        self._callback = CALLBACK(self._fill_buffer)
        queue = c_void_p()
        rc = _at.AudioQueueNewOutput(byref(fmt), self._callback, None, None, None, 0, byref(queue))
        if rc != 0 or not queue.value:
            self._queue = None
            return
        self._queue = queue.value

        buffer_bytes = max(2048, bytes_per_frame * sample_rate // 10)  # ~100 ms
        for _ in range(4):
            buf = c_void_p()
            if _at.AudioQueueAllocateBuffer(self._queue, buffer_bytes, byref(buf)) != 0:
                continue
            self._buffers.append(buf.value)
            _at.AudioQueueEnqueueBuffer(self._queue, buf.value, 0, None)

        _at.AudioQueueStart(self._queue, None)

    @property
    def active(self) -> bool:
        return self._queue is not None

    def _fill_buffer(self, _user_data, _queue, in_buffer) -> None:
        if not self._queue:
            return
        buf = ctypes.cast(in_buffer, POINTER(AudioQueueBuffer)).contents
        capacity = buf.mAudioDataBytesCapacity
        with self._lock:
            n = min(capacity, len(self._data))
            data = bytes(self._data[:n])
            del self._data[:n]
        if n:
            ctypes.memmove(buf.mAudioData, data, n)
        buf.mAudioDataByteSize = n
        _at.AudioQueueEnqueueBuffer(self._queue, in_buffer, 0, None)

    def write(self, data: bytes) -> None:
        if not self._queue:
            return
        with self._lock:
            self._data += data

    def stop(self) -> None:
        if self._queue:
            _at.AudioQueueStop(self._queue, 1)
            _at.AudioQueueDispose(self._queue, 1)
            self._queue = None
            self._buffers.clear()
