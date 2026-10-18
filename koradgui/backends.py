"""All device I/O is owned by the controller worker, including open/close."""
from collections import deque
from dataclasses import dataclass, replace
import math
import struct
import time
from typing import Protocol

import serial

from .devices import Family
from .fnirsi_protocol import StreamDecoder, decode_state, encode, read, write_byte, write_float
from .koradserial import KoradSerial


class ProtocolError(ValueError):
  pass


@dataclass(frozen=True)
class Settings:
  voltage: float = 0.0
  current: float = 0.0
  output: bool = False
  ocp: bool = False
  ovp: bool = False
  ocp_value: float = 0.0
  ovp_value: float = 0.0
  max_voltage: float = 30.0
  max_current: float = 5.1
  ocp_ceiling: float = 5.1
  ovp_ceiling: float = 30.0


class SupplyBackend(Protocol):
  family: Family
  settings: Settings
  def connect(self) -> Settings: ...
  def read_settings(self) -> Settings: ...
  def poll_measurements(self) -> list[tuple[float, float, float]]: ...
  def set_setpoints(self, voltage: float, current: float) -> None: ...
  def set_status(self, output: bool, ocp: bool, ovp: bool) -> None: ...
  def set_lock(self, locked: bool) -> None: ...
  def set_protection(self, name: str, value: float) -> Settings: ...
  def shutdown(self, send_off: bool) -> None: ...


class KoradBackend:
  family = Family.KORAD

  def __init__(self, port, factory=KoradSerial):
    self.port = port
    self.factory = factory
    self.device = None
    self.settings = Settings()

  def connect(self):
    self.device = self.factory(self.port)
    # Preserve the original connection command order and protection policy.
    self.set_lock(True)
    self.set_status(False, False, False)
    return self.read_settings()

  def read_settings(self):
    status = self.device.status
    channel = self.device.channels[0]
    voltage, current = channel.voltage, channel.current
    if voltage is None or current is None:
      raise ProtocolError('Invalid Korad setpoint response')
    self.settings = Settings(voltage, current, status.output, status.ocp, status.ovp)
    return self.settings

  def poll_measurements(self):
    voltage, current = self.device.channels[0].output_pair
    if voltage is None or current is None:
      raise ProtocolError('Invalid Korad measurement')
    return [(time.monotonic(), voltage, current)]

  def set_setpoints(self, voltage, current):
    self.device.channels[0].voltage = voltage
    self.device.channels[0].current = current
    self.settings = replace(self.settings, voltage=voltage, current=current)

  def set_status(self, output, ocp, ovp):
    self.device.ocp.set(ocp)
    self.device.ovp.set(ovp)
    self.device.output.set(output)
    self.settings = replace(self.settings, output=output, ocp=ocp, ovp=ovp)

  def set_lock(self, locked):
    self.device.lock.set(locked)

  def set_protection(self, name, value):
    raise ProtocolError('Korad uses protection switches')

  def shutdown(self, send_off):
    if self.device is None:
      return
    try:
      if send_off:
        try:
          self.device.output.set(False)
        finally:
          self.set_lock(False)
    finally:
      self.device.close()


class FnirsiBackend:
  family = Family.FNIRSI

  def __init__(self, port, factory=serial.Serial, timeout=1.0):
    self.port = port
    self.factory = factory
    self.timeout = timeout
    self.device = None
    self.parser = StreamDecoder()
    self.settings = Settings(max_current=5.0)
    self._frames = {}
    self._versions = {}
    self._samples = deque()
    self._last_send = 0.0
    self._last_measurement = 0.0
    self._verified = False

  def _send(self, packet):
    delay = 0.05 - (time.monotonic() - self._last_send)
    if delay > 0:
      time.sleep(delay)
    count = self.device.write(packet)
    if count != len(packet):
      raise ProtocolError('Incomplete FNIRSI serial write')
    self._last_send = time.monotonic()

  def _pump(self):
    chunk = self.device.read(min(max(self.device.in_waiting, 1), 4096))
    for frame in self.parser.feed(chunk):
      self._frames[frame.register] = frame.payload
      self._versions[frame.register] = self._versions.get(frame.register, 0) + 1
      if frame.register == 0xC3:
        voltage, current, _ = struct.unpack('<fff', frame.payload)
        if not all(math.isfinite(v) for v in (voltage, current)):
          raise ProtocolError('Invalid FNIRSI measurement')
        self._last_measurement = time.monotonic()
        self._samples.append((self._last_measurement, voltage, current))
      elif frame.register == 0xDB:
        self.settings = replace(self.settings, output=bool(frame.payload[0]))
      elif frame.register in (0xE2, 0xE3):
        value, = struct.unpack('<f', frame.payload)
        if not math.isfinite(value) or value < 0:
          raise ProtocolError('Invalid FNIRSI capability')
        key, cap = ('max_voltage', 30.0) if frame.register == 0xE2 else ('max_current', 5.0)
        self.settings = replace(self.settings, **{key: min(value, cap)})
    return bool(chunk)

  def _request(self, register):
    # Drain buffered telemetry before taking the response generation baseline.
    # There is no transaction ID on the wire; requests are strictly serialized.
    for _ in range(32):
      if not self.device.in_waiting:
        break
      self._pump()
    version = self._versions.get(register, 0)
    self._send(read(register))
    deadline = time.monotonic() + self.timeout
    while time.monotonic() < deadline:
      self._pump()
      if self._versions.get(register, 0) > version:
        return self._frames[register]
    self.parser.reset()
    raise ProtocolError(f'FNIRSI register {register:02X} timed out')

  def connect(self):
    self.device = self.factory(self.port, 115200, timeout=0.02, write_timeout=self.timeout,
                               bytesize=8, parity='N', stopbits=1, rtscts=False)
    self.device.rts = True
    self._send(encode(0xC1, 0, b'\x01'))
    model = self._request(0xDE).rstrip(b'\x00')
    if model != b'DPS-150':
      raise ProtocolError(f'Expected DPS-150, received {model!r}')
    self.read_settings()
    self._verified = True
    self.set_status(False, False, False)
    self._last_measurement = time.monotonic()
    self._samples.clear()
    return self.settings

  def read_settings(self):
    state = decode_state(self._request(0xFF))
    required = ('voltage_setpoint', 'current_limit', 'maximum_voltage', 'maximum_current',
                'ocp', 'ovp', 'ocp_ceiling', 'ovp_ceiling')
    if not all(k in state and math.isfinite(state[k]) and state[k] >= 0 for k in required):
      raise ProtocolError('Invalid or incomplete FNIRSI settings/limits')
    if state['ocp_ceiling'] <= 0 or state['ovp_ceiling'] <= 0:
      raise ProtocolError('Invalid FNIRSI protection ceilings')
    if state['output_enabled_raw'] not in (0, 1):
      raise ProtocolError('Invalid FNIRSI output state')
    self.settings = Settings(
      voltage=state['voltage_setpoint'], current=state['current_limit'],
      output=bool(state['output_enabled_raw']), ocp_value=state['ocp'], ovp_value=state['ovp'],
      max_voltage=min(30.0, state['maximum_voltage']),
      max_current=min(5.0, state['maximum_current']),
      ocp_ceiling=state['ocp_ceiling'], ovp_ceiling=state['ovp_ceiling'])
    return self.settings

  def poll_measurements(self):
    self._pump()
    if time.monotonic() - self._last_measurement > 2.0:
      raise ProtocolError('FNIRSI telemetry timed out')
    result = list(self._samples)
    self._samples.clear()
    return result

  @staticmethod
  def _validate(value, maximum, name, positive=False):
    if not math.isfinite(value) or value < 0 or value > maximum or (positive and value == 0):
      raise ProtocolError(f'{name} must be {"> 0 and " if positive else ""}within 0–{maximum:g}')

  def _write_float_verified(self, register, value, tolerance):
    self._send(write_float(register, value))
    payload = self._request(register)
    actual, = struct.unpack('<f', payload)
    if not math.isfinite(actual) or abs(actual - value) > tolerance:
      raise ProtocolError(f'FNIRSI {register:02X} readback {actual:g} differs from {value:g}')
    return actual

  def set_setpoints(self, voltage, current):
    # Capabilities can change with the input supply. Consume pending telemetry
    # before validating, rather than relying on stale limits from connection.
    for _ in range(32):
      if not self.device.in_waiting:
        break
      self._pump()
    self._validate(voltage, self.settings.max_voltage, 'Voltage')
    self._validate(current, self.settings.max_current, 'Current')
    actual_v = self._write_float_verified(0xC1, voltage, 0.0101)
    actual_i = self._write_float_verified(0xC2, current, 0.0011)
    self.settings = replace(self.settings, voltage=actual_v, current=actual_i)

  def set_status(self, output, ocp, ovp):
    self._send(write_byte(0xDB, int(output)))
    actual = self._request(0xDB)
    if actual != bytes((int(output),)):
      raise ProtocolError('FNIRSI output readback differs from requested state')
    self.settings = replace(self.settings, output=output)

  def set_lock(self, locked):
    # Opening the remote session locks the panel. Normal shutdown releases it.
    pass

  def set_protection(self, name, value):
    if name not in ('ocp', 'ovp'):
      raise ProtocolError('Unknown protection')
    self._validate(value, getattr(self.settings, name + '_ceiling'), name.upper(), positive=True)
    register, tolerance = (0xD2, 0.0011) if name == 'ocp' else (0xD1, 0.0101)
    actual = self._write_float_verified(register, value, tolerance)
    self.settings = replace(self.settings, **{name + '_value': actual})
    return self.settings

  def shutdown(self, send_off):
    if self.device is None:
      return
    try:
      try:
        if send_off and self._verified:
          self._send(write_byte(0xDB, 0))
      finally:
        self._send(encode(0xC1, 0, b'\x00'))
    finally:
      self.device.close()
