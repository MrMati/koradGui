from enum import Enum, auto
from queue import Queue, Empty
from threading import Event as ThreadEvent, Thread

from serial.serialutil import SerialException

from .backends import SupplyBackend
from .devices import Family
from .koradserial import DisconnectedError


class Cmd(Enum):
  READ = auto()
  WRITE = auto()
  PROTECTION = auto()


class CmdOption(Enum):
  SETPOINT = auto()
  OUTPUT_READING = auto()
  LOCK = auto()
  STATUS = auto()


CmdOptions = set[CmdOption]


class Event(Enum):
  DISCONNECTED = 0
  READ_FINISHED = 1
  READY = 2
  PROTECTION_FINISHED = 3
  ERROR = 4
  STATE_UPDATED = 5


class PowerSupplyCtrl:
  def __init__(self, backend: SupplyBackend):
    self.stream_output = False
    self.voltage = self.current = 0.0
    self.voltage_output = self.current_output = 0.0
    self.lock = False
    self.ocp = self.ovp = self.output = False
    self.settings = None
    self.ready = False
    self.family = backend.family
    self._backend = backend
    self._cmd_queue = Queue()
    self._event_queue = Queue()
    self._data_queue = Queue()
    self._thread = None
    self._closed = False
    self._pending = 0
    self._stop = ThreadEvent()
    self._send_off = False

  def _update_settings(self, settings):
    self.settings = settings
    self.voltage, self.current = settings.voltage, settings.current
    self.output, self.ocp, self.ovp = settings.output, settings.ocp, settings.ovp

  def _thread_main(self):
    try:
      self._update_settings(self._backend.connect())
      self.lock = True
      self.ready = True
      self._event_queue.put((Event.READY, None))
      while not self._stop.is_set():
        try:
          cmd, options, values = self._cmd_queue.get(timeout=0.01)
        except Empty:
          if self.stream_output:
            for sample in self._backend.poll_measurements():
              _, self.voltage_output, self.current_output = sample
              self._data_queue.put(sample)
            # FNIRSI can push output state and available limits without a GET.
            if self.family is Family.FNIRSI and self.settings != self._backend.settings:
              self.settings = self._backend.settings
              self.output = self.settings.output
              self._event_queue.put((Event.STATE_UPDATED, None))
          continue
        try:
          if self._stop.is_set():
            break
          if cmd is Cmd.READ:
            if options & {CmdOption.STATUS, CmdOption.SETPOINT}:
              self._update_settings(self._backend.read_settings())
              self._event_queue.put((Event.READ_FINISHED, None))
            if CmdOption.OUTPUT_READING in options:
              for sample in self._backend.poll_measurements():
                self._data_queue.put(sample)
          elif cmd is Cmd.PROTECTION:
            name, value = values
            try:
              self.settings = self._backend.set_protection(name, value)
            except ValueError as error:
              # A failed readback does not prove the write failed. Refresh both
              # thresholds; report actual state, without retries or rollback.
              self.settings = self._backend.read_settings()
              self._event_queue.put((Event.ERROR, str(error)))
            self._event_queue.put((Event.PROTECTION_FINISHED, None))
          else:
            voltage, current, output, ocp, ovp, locked = values
            if CmdOption.LOCK in options:
              self._backend.set_lock(locked)
            if CmdOption.SETPOINT in options:
              self._backend.set_setpoints(voltage, current)
            if CmdOption.STATUS in options:
              self._backend.set_status(output, ocp, ovp)
            if self.family is Family.FNIRSI:
              self.settings = self._backend.settings
              self.output = self.settings.output
              self._event_queue.put((Event.STATE_UPDATED, None))
        finally:
          self._pending -= 1
          self._cmd_queue.task_done()
    except (DisconnectedError, SerialException, ValueError, IndexError, OSError) as error:
      self.stream_output = False
      self.ready = False
      self._send_off = False
      self._event_queue.put((Event.DISCONNECTED, str(error) or 'Device communication failed'))
    finally:
      self.ready = False
      try:
        self._backend.shutdown(self._send_off)
      except (DisconnectedError, SerialException, ValueError, IndexError, OSError) as error:
        self._event_queue.put((Event.ERROR, f'Disconnect: {error}'))

  def start(self):
    if self._thread is None:
      self._thread = Thread(target=self._thread_main, daemon=True)
      self._thread.start()

  def close(self, send_off=True):
    if self._closed:
      return
    self._closed = True
    self._send_off = send_off
    self._stop.set()
    if self._thread is not None:
      self._thread.join()
      self._thread = None

  def read_output_data(self):
    data = []
    while True:
      try:
        data.append(self._data_queue.get_nowait())
      except Empty:
        return data

  def read_event(self):
    try:
      return self._event_queue.get_nowait()
    except Empty:
      return None

  def _enqueue(self, cmd, options, values=None):
    if not self.ready or self._closed:
      return
    self._pending += 1
    self._cmd_queue.put((cmd, options, values))

  def read_output(self):
    self._enqueue(Cmd.READ, {CmdOption.OUTPUT_READING})

  def read_settings(self):
    self._enqueue(Cmd.READ, {CmdOption.SETPOINT, CmdOption.STATUS})

  def write_setpoint(self):
    self.write_custom({CmdOption.SETPOINT})

  def write_custom(self, options):
    # Capture values when enqueued; later UI edits must not alter old commands.
    values = self.voltage, self.current, self.output, self.ocp, self.ovp, self.lock
    self._enqueue(Cmd.WRITE, options.copy(), values)

  def write_all(self):
    self.write_custom({CmdOption.SETPOINT, CmdOption.LOCK, CmdOption.STATUS})

  def write_protection(self, name, value):
    self._enqueue(Cmd.PROTECTION, set(), (name, value))

  @property
  def closed(self):
    return self._closed

  @property
  def pending(self):
    return not self.ready or self._pending > 0
