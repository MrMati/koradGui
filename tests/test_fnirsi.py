import json
from pathlib import Path
import struct
import time
from unittest import TestCase
from unittest.mock import patch
from serial import SerialException

from koradgui.backends import FnirsiBackend, ProtocolError
from koradgui.fnirsi_protocol import StreamDecoder, decode, decode_state, encode, read, write_float

FIXTURES = Path(__file__).parent / 'fixtures'


def captured_frames(name):
  decoder = StreamDecoder()
  frames = []
  for line in (FIXTURES / name).read_text().splitlines():
    row = json.loads(line)
    if row['direction'] == 'rx':
      frames.extend(decoder.feed(bytes.fromhex(row['hex'])))
  return frames


STATE = next(f.payload for f in captured_frames('v1.2-state.jsonl') if f.register == 0xff)


class FakeSerial:
  def __init__(self, *args, **kwargs):
    self.args, self.kwargs = args, kwargs
    self.pending = bytearray()
    self.writes = []
    self.state = bytearray(STATE)
    self.state[107] = 1  # initially running: connect must turn it off
    self.model = b'DPS-150'
    self.closed = False
    self.rts = False
    self.silent = set()
    self.reject = set()

  @property
  def in_waiting(self):
    return len(self.pending)

  def respond(self, register, payload):
    self.pending.extend(encode(0xa1, register, payload, header=0xf0))

  def read(self, count):
    count = min(count, 3)  # deliberately fragment every reply
    data = bytes(self.pending[:count])
    del self.pending[:count]
    return data

  def write(self, packet):
    self.writes.append(decode(packet))
    frame = self.writes[-1]
    if frame.command == 0xb1 and frame.register not in self.reject:
      offset = {0xc1: 4, 0xc2: 8, 0xd1: 76, 0xd2: 80, 0xdb: 107}[frame.register]
      self.state[offset:offset + len(frame.payload)] = frame.payload
    elif frame.command == 0xa1 and frame.register not in self.silent:
      if frame.register == 0xde:
        # Unsolicited measurement interleaved with identity response.
        self.respond(0xc3, struct.pack('<fff', 0.42, 0.67, 0.2814))
        self.respond(0xde, self.model)
      elif frame.register == 0xff:
        self.respond(0xff, self.state)
      else:
        offset, size = {0xc1: (4, 4), 0xc2: (8, 4), 0xd1: (76, 4),
                        0xd2: (80, 4), 0xdb: (107, 1)}[frame.register]
        self.respond(frame.register, self.state[offset:offset + size])
    return len(packet)

  def close(self):
    self.closed = True


class WireTests(TestCase):
  def test_capture_replay_at_every_split(self):
    for name in ('v1.2-info.jsonl', 'v1.2-state.jsonl'):
      rows = [json.loads(line) for line in (FIXTURES / name).read_text().splitlines()]
      wire = b''.join(bytes.fromhex(row['hex']) for row in rows if row['direction'] == 'rx')
      expected = captured_frames(name)
      for split in range(len(wire) + 1):
        decoder = StreamDecoder()
        self.assertEqual(decoder.feed(wire[:split]) + decoder.feed(wire[split:]), expected)
        self.assertFalse(decoder.buffer)
    state = decode_state(STATE)
    self.assertEqual(state['otp_ceiling'], 99)
    self.assertEqual(state['reserved_110_raw'], 1)
    self.assertAlmostEqual(state['maximum_current'], 5.1, places=5)

  def test_vectors_noise_bad_checksum_and_unaligned_state(self):
    self.assertEqual(read(0xff).hex(' '), 'f1 a1 ff 01 00 00')
    self.assertEqual(write_float(0xc1, 12.3).hex(' '), 'f1 b1 c1 04 cd cc 44 41 e3')
    good = encode(0xa1, 0xc0, struct.pack('<f', 5), header=0xf0)
    decoder = StreamDecoder()
    self.assertEqual(decoder.feed(b'noise' + good[:-1] + b'\x00' + good), [decode(good)])
    with self.assertRaises(ValueError):
      decode(bytes.fromhex('f1 b0 00 01 05 01'))
    with self.assertRaises(ValueError):
      decode_state(b'\x00' * 118)
    payload = bytearray(STATE)
    struct.pack_into('<f', payload, 99, 1.25)
    self.assertEqual(decode_state(payload)['capacity_ah'], 1.25)


class BackendTests(TestCase):
  def setUp(self):
    self.serial = FakeSerial()
    self.backend = FnirsiBackend('/dev/fake', factory=lambda *a, **k: self.serial, timeout=0.01)
    # Avoid pacing wall time without weakening the real packet/request logic.
    self.sleep = patch('koradgui.backends.time.sleep')
    self.sleep.start()
    self.addCleanup(self.sleep.stop)

  def test_connection_preserves_settings_and_shutdown_is_off_then_release(self):
    before = self.serial.state[:107]
    settings = self.backend.connect()
    self.assertFalse(settings.output)
    self.assertEqual(self.serial.state[:107], before)
    self.assertTrue(self.serial.rts)
    self.assertEqual(settings.max_current, 5)
    self.assertEqual([(f.command, f.register) for f in self.serial.writes],
                     [(0xc1, 0), (0xa1, 0xde), (0xa1, 0xff), (0xb1, 0xdb), (0xa1, 0xdb)])
    self.backend.shutdown(True)
    last = self.serial.writes[-2:]
    self.assertEqual([(f.command, f.register, f.payload) for f in last],
                     [(0xb1, 0xdb, b'\x00'), (0xc1, 0, b'\x00')])
    self.assertTrue(self.serial.closed)

  def test_setpoints_and_protection_readback(self):
    self.backend.connect()
    self.backend.set_setpoints(0.42, 0.67)
    self.assertAlmostEqual(self.backend.settings.voltage, 0.42, places=5)
    self.backend.set_protection('ocp', 2.0)
    self.assertEqual(self.backend.settings.ocp_value, 2)
    self.backend.set_protection('ovp', 10)
    self.assertEqual(self.backend.settings.ovp_value, 10)
    writes = [f.register for f in self.serial.writes if f.command == 0xb1]
    self.assertEqual(writes, [0xdb, 0xc1, 0xc2, 0xd2, 0xd1])
    self.serial.reject.add(0xd2)
    with self.assertRaisesRegex(ProtocolError, 'readback'):
      self.backend.set_protection('ocp', 3)
    self.assertEqual(self.backend.read_settings().ocp_value, 2)

  def test_invalid_values_never_send_writes(self):
    self.backend.connect()
    count = len(self.serial.writes)
    for value in (0, -1, float('nan'), float('inf'), 100):
      with self.assertRaises(ProtocolError):
        self.backend.set_protection('ocp', value)
    with self.assertRaises(ProtocolError):
      self.backend.set_setpoints(31, 1)
    with self.assertRaises(ProtocolError):
      self.backend.set_setpoints(5, 5.1)
    self.assertEqual(len(self.serial.writes), count)

  def test_dynamic_limits_and_fresh_samples(self):
    self.backend.connect()
    self.serial.respond(0xe2, struct.pack('<f', 3))
    with self.assertRaises(ProtocolError):
      self.backend.set_setpoints(4, 1)
    self.serial.respond(0xc3, struct.pack('<fff', 1, 2, 2))
    samples = []
    while self.serial.in_waiting:
      samples.extend(self.backend.poll_measurements())
    self.assertEqual([sample[1:] for sample in samples], [(1, 2)])
    self.assertEqual(self.backend.poll_measurements(), [])
    self.backend._last_measurement = time.monotonic() - 3
    with self.assertRaisesRegex(ProtocolError, 'telemetry timed out'):
      self.backend.poll_measurements()

  def test_timeout_and_wrong_model_do_not_rewrite_settings(self):
    self.serial.model = b'OTHER'
    with self.assertRaises(ProtocolError):
      self.backend.connect()
    self.backend.shutdown(True)
    self.assertFalse(any(f.command == 0xb1 for f in self.serial.writes))
    self.assertTrue(self.serial.closed)

  def test_read_timeout_resets_partial_frame(self):
    self.backend.connect()
    self.serial.silent.add(0xc1)
    self.serial.pending.extend(b'\xf0\xa1\xc1\x04\x00')
    with self.assertRaisesRegex(ProtocolError, 'timed out'):
      self.backend._request(0xc1)
    self.assertFalse(self.backend.parser.buffer)

  def test_shutdown_releases_session_and_closes_after_output_error(self):
    self.backend.connect()
    original = self.serial.write
    def failing_output(packet):
      if decode(packet).command == 0xb1:
        raise SerialException('output-off failed')
      return original(packet)
    self.serial.write = failing_output
    with self.assertRaisesRegex(SerialException, 'output-off failed'):
      self.backend.shutdown(True)
    self.assertEqual((self.serial.writes[-1].command, self.serial.writes[-1].payload), (0xc1, b'\x00'))
    self.assertTrue(self.serial.closed)
