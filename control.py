from enum import Enum, auto
from typing import Optional, Callable, Any

from koradserial import KoradSerial, OutputPair, DisconnectedError, CommunicationError
from threading import Thread
from queue import Queue, Empty


class Cmd(Enum):
  STOP = auto()
  READ = auto()
  WRITE = auto()


class CmdOption(Enum):
  SETPOINT = auto()
  OUTPUT_READING = auto()
  LOCK = auto()
  STATUS = auto()


CmdOptions = set[CmdOption]


class Event(Enum):
  DISCONNECTED = 0
  READ_FINISHED = 1


class PowerSupplyCtrl:
  def __init__(self, ps_ctl: KoradSerial):
    self.stream_output = False

    self.voltage = 0.0
    self.current = 0.0
    self.voltage_output = 0.0
    self.current_output = 0.0
    self.lock = False  # cannot be read from PS
    self.beep = False  # supported removed
    self.ocp = False
    self.ovp = False
    self.output = False

    self._ps_ctl = ps_ctl
    self._cmd_queue: Queue[tuple[Cmd, CmdOptions]] = Queue()
    self._event_queue: Queue[tuple[Event, Any]] = Queue()
    self._data_queue: Queue[OutputPair] = Queue()
    self._thread: Optional[Thread] = None
    self._closed = False
    self._pending = False

  def _thread_main(self):
    while True:
      if self.stream_output:
        self._call_error_handle(self._read, {CmdOption.OUTPUT_READING})
        self._data_queue.put((self.voltage_output, self.current_output))

      try:
        cmd, options = self._cmd_queue.get(timeout=0.01)
      except Empty:
        continue
      print(f"{cmd.name} - {[opt.name for opt in options]}")
      if cmd is Cmd.STOP:
        return

      self._pending = True

      func = {
        Cmd.READ: self._read,
        Cmd.WRITE: self._write,
      }[cmd]
      self._call_error_handle(func, options, set_pending=True)

      if cmd is Cmd.READ:
        self._event_queue.put((Event.READ_FINISHED, None))

      self._cmd_queue.task_done()

  def start(self):
    if self._thread is not None:
      return

    self._thread = Thread(target=self._thread_main, daemon=True)
    self._thread.start()

  def close(self):
    if self._closed:
      return
    self._closed = True
    if self._thread is not None:
      self._cmd_queue.put((Cmd.STOP, set()))
      self._thread.join()
      self._thread = None
    self._ps_ctl.close()

  def read_output_data(self) -> list[OutputPair]:
    data = []
    while not self._data_queue.empty():
      try:
        data.append(self._data_queue.get_nowait())
      except Empty:
        pass
    return data

  def read_event(self) -> Optional[tuple[Event, Any]]:
    try:
      return self._event_queue.get_nowait()
    except Empty:
      return None

  def read_output(self):
    self._cmd_queue.put((Cmd.READ, {CmdOption.OUTPUT_READING}))

  def read_settings(self):
    self._cmd_queue.put((Cmd.READ, {CmdOption.SETPOINT, CmdOption.STATUS}))

  def write_setpoint(self):
    self._cmd_queue.put((Cmd.WRITE, {CmdOption.SETPOINT}))

  def write_custom(self, options: CmdOptions):
    self._cmd_queue.put((Cmd.WRITE, options))

  def write_all(self):
    self._cmd_queue.put((Cmd.WRITE, {CmdOption.SETPOINT, CmdOption.LOCK, CmdOption.STATUS}))

  def _read(self, options: CmdOptions):
    if CmdOption.STATUS in options:
      status = self._ps_ctl.status
      self.ocp = status.ocp
      self.ovp = status.ovp
      self.output = status.output
    if CmdOption.SETPOINT in options:
      self.voltage = self._ps_ctl.channels[0].voltage
      self.current = self._ps_ctl.channels[0].current
    if CmdOption.OUTPUT_READING in options:
      self.voltage_output, self.current_output = self._ps_ctl.channels[0].output_pair

  def _write(self, options):
    if CmdOption.STATUS in options:
      self._ps_ctl.ocp.set(self.ocp)
      self._ps_ctl.ovp.set(self.ovp)
      self._ps_ctl.output.set(self.output)
    if CmdOption.LOCK in options:
      self._ps_ctl.lock.set(self.lock)
    if CmdOption.SETPOINT in options:
      self._ps_ctl.channels[0].voltage = self.voltage
      self._ps_ctl.channels[0].current = self.current

  @property
  def closed(self):
    return self._closed

  @property
  def pending(self):
    return self._pending

  def _call_error_handle(self, func: Callable, options, set_pending: bool = False):
    if set_pending:
      self._pending = True
    try:
      func(options)
    except DisconnectedError as e:
      self._event_queue.put((Event.DISCONNECTED, e))
    except CommunicationError as e:
      self._event_queue.put((Event.DISCONNECTED, e))
    finally:
      if set_pending:
        self._pending = False
