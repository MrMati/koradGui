"""Offline DPS-150 codec derived from the supplied Windows app. No device I/O.

See docs/fnirsi-protocol.md for the wire format and verification boundary.
"""
from dataclasses import dataclass
import json
import struct
import sys


FLOAT_FIELDS = [
    'input_voltage', 'voltage_setpoint', 'current_limit',
    'output_voltage', 'output_current', 'output_power', 'temperature',
] + [f'preset_{i}_{kind}' for i in range(1, 7) for kind in ('voltage', 'current')] + [
    'ovp', 'ocp', 'opp', 'otp', 'lvp',
]
TAIL_FLOATS = {
    99: 'capacity_ah', 103: 'energy_wh',
    111: 'maximum_voltage', 115: 'maximum_current',
    119: 'ovp_ceiling', 123: 'ocp_ceiling', 127: 'opp_ceiling',
    131: 'otp_ceiling', 135: 'lvp_ceiling',
}
BYTE_FIELDS = {
    96: 'brightness', 97: 'volume', 98: 'metering_raw',
    107: 'output_enabled_raw', 108: 'protection_raw', 109: 'regulation_raw',
    110: 'reserved_110_raw',
}
FLOAT_REGISTERS = {0xC0, 0xC1, 0xC2, 0xC4, *range(0xC5, 0xD6), 0xD9, 0xDA, 0xE2, 0xE3}
BYTE_REGISTERS = {0xD6, 0xD7, 0xD8, 0xDB, 0xDC, 0xDD, 0xE1}


def encode(command: int, register: int, payload: bytes = b'\x00', *, header: int = 0xF1) -> bytes:
    """Always calculate the checksum afresh, including for baud commands."""
    if len(payload) > 255:
        raise ValueError('payload exceeds one-byte length')
    return bytes((header, command, register, len(payload))) + payload + bytes((
        (register + len(payload) + sum(payload)) & 255,
    ))


def read(register: int) -> bytes:
    return encode(0xA1, register)


def write_float(register: int, value: float) -> bytes:
    return encode(0xB1, register, struct.pack('<f', value))


def write_byte(register: int, value: int) -> bytes:
    return encode(0xB1, register, bytes((value,)))


@dataclass(frozen=True)
class Frame:
    header: int
    command: int
    register: int
    payload: bytes


def decode(packet: bytes) -> Frame:
    if len(packet) < 5 or packet[0] not in (0xF0, 0xF1):
        raise ValueError('invalid frame header or size')
    if len(packet) != 5 + packet[3]:
        raise ValueError('frame length mismatch')
    if sum(packet[2:-1]) & 255 != packet[-1]:
        raise ValueError('checksum mismatch')
    return Frame(packet[0], packet[1], packet[2], packet[4:-1])


class StreamDecoder:
    """RX A1 stream, retaining fragments and skipping noise/bad checksums.

    A plausible but corrupted length may require an explicit reset after a
    transport timeout. A partial payload can contain header-like bytes, so it
    is not safe to skip forward merely because another header is visible.
    """
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, chunk: bytes) -> list[Frame]:
        self.buffer.extend(chunk)
        frames = []
        while self.buffer:
            if self.buffer[0] != 0xF0:
                del self.buffer[0]
                continue
            if len(self.buffer) < 4:
                break
            register, length = self.buffer[2:4]
            expected = (4 if register in FLOAT_REGISTERS else
                        1 if register in BYTE_REGISTERS else
                        12 if register == 0xC3 else None)
            if (self.buffer[1] != 0xA1 or
                    (expected is not None and length != expected) or
                    (register == 0xFF and length < 119)):
                del self.buffer[0]
                continue
            size = length + 5
            if len(self.buffer) < size:
                break
            try:
                frame = decode(bytes(self.buffer[:size]))
            except ValueError:
                del self.buffer[0]
                continue
            frames.append(frame)
            del self.buffer[:size]
        return frames

    def reset(self) -> bytes:
        pending = bytes(self.buffer)
        self.buffer.clear()
        return pending


def decode_state(payload: bytes) -> dict:
    """139-byte vendor layout, with defensive support for a 119-byte core.

    Only 139-byte states are corroborated by the included external captures.
    Preserve the unknown byte and any future extension instead of guessing.
    """
    if len(payload) < 119:
        raise ValueError('state shorter than 119-byte core')
    result = {name: struct.unpack_from('<f', payload, 4 * i)[0]
              for i, name in enumerate(FLOAT_FIELDS)}
    result.update({name: payload[offset] for offset, name in BYTE_FIELDS.items()})
    result.update({name: struct.unpack_from('<f', payload, offset)[0]
                   for offset, name in TAIL_FLOATS.items() if offset + 4 <= len(payload)})
    result['payload_length'] = len(payload)
    # Retain every byte, including an incomplete optional ceiling or extension.
    result['raw_hex'] = payload.hex(' ')
    return result


def interpret(frame: Frame):
    if frame.header != 0xF0 or frame.command != 0xA1:
        return {'raw_hex': frame.payload.hex(' ')}
    if frame.register == 0xFF:
        return decode_state(frame.payload)
    if frame.register in FLOAT_REGISTERS and len(frame.payload) == 4:
        return struct.unpack('<f', frame.payload)[0]
    if frame.register == 0xC3 and len(frame.payload) == 12:
        return dict(zip(('voltage', 'current', 'power'), struct.unpack('<fff', frame.payload)))
    if frame.register in BYTE_REGISTERS and len(frame.payload) == 1:
        return frame.payload[0]
    if frame.register in (0xDE, 0xDF, 0xE0):
        return {'text': frame.payload.rstrip(b'\x00').decode('ascii', errors='replace'),
                'raw_hex': frame.payload.hex(' ')}
    return {'raw_hex': frame.payload.hex(' ')}


if __name__ == '__main__':
    parser = StreamDecoder()
    data = bytes.fromhex(' '.join(sys.argv[1:]) if len(sys.argv) > 1 else sys.stdin.read())
    for frame in parser.feed(data):
        print(json.dumps({'register': f'{frame.register:02X}', 'value': interpret(frame)}))
    if parser.buffer:
        sys.exit(f'incomplete trailing frame: {parser.buffer.hex(" ")}')
