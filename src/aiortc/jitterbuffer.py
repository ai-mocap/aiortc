from typing import List, Optional
from datetime import datetime
from dataclasses import dataclass

from av import VideoFrame
from .rtp import RtpPacket

MAX_MISORDER = 100


@dataclass
class JitterFrame:
    data: bytes
    packets: List[RtpPacket]
    timestamp: int
    ntp_timestamp: datetime
    rtp_diff: int


@dataclass
class VideoFrameExt:
    frame: VideoFrame
    ntp_timestamp: datetime
    encoded_frame: JitterFrame


class JitterBuffer:
    def __init__(self, capacity: int, prefetch: int = 0) -> None:
        assert capacity & (capacity - 1) == 0, "capacity must be a power of 2"
        self._capacity = capacity
        self._origin: Optional[int] = None
        self._packets: List[Optional[RtpPacket]] = [None for i in range(capacity)]
        self._prefetch = prefetch

    @property
    def capacity(self) -> int:
        return self._capacity

    def add(self, packet: RtpPacket) -> Optional[JitterFrame]:
        if self._origin is None:
            self._origin = packet.sequence_number
        elif packet.sequence_number <= self._origin - MAX_MISORDER:
            self.remove(self.capacity)
            self._origin = packet.sequence_number
        elif packet.sequence_number < self._origin:
            return None

        delta = packet.sequence_number - self._origin
        if delta >= 2 * self.capacity:
            # received packet is so far beyond capacity we cannot keep any
            # previous packets, so reset the buffer
            self.remove(self.capacity)
            self._origin = packet.sequence_number
        elif delta >= self.capacity:
            # remove just enough packets to fit the received packets
            excess = delta - self.capacity + 1
            self.remove(excess)

        pos = packet.sequence_number % self._capacity
        self._packets[pos] = packet

        return self._remove_frame(packet.sequence_number)

    def _remove_frame(self, sequence_number: int) -> Optional[JitterFrame]:
        frame = None
        frames = 0
        packets = []
        remove = 0
        timestamp = None
        ntp_timestamp = None

        for count in range(self.capacity):
            pos = (self._origin + count) % self._capacity
            packet = self._packets[pos]
            if packet is None:
                break
            if timestamp is None:
                timestamp = packet.timestamp
                ntp_timestamp = packet.ntp_timestamp
                rtp_diff = packet.rtp_diff
            elif packet.timestamp != timestamp:
                # we now have a complete frame, only store the first one
                if frame is None:
                    frame = JitterFrame(
                        data=b"".join([x._data for x in packets]),
                        timestamp=timestamp,
                        ntp_timestamp=ntp_timestamp,
                        packets=packets,
                        rtp_diff=rtp_diff,
                    )
                    remove = count

                # check we have prefetched enough
                frames += 1
                if frames >= self._prefetch:
                    self.remove(remove)
                    return frame

                # start a new frame
                packets = []
                timestamp = packet.timestamp

            packets.append(packet)

        return None

    def remove(self, count: int) -> None:
        assert count <= self._capacity
        for i in range(count):
            pos = self._origin % self._capacity
            self._packets[pos] = None
            self._origin += 1
