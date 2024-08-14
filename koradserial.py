""" Serial communication with Korad KA3xxxP power supplies.

The intent is to give easy access to the power supply as Python objects,
eliminating the need to know special codes.

The object supports the python `with` statement to release the serial
port automatically:

from koradserial import KoradSerial

with KoradSerial('/dev/tty.usbmodemfd121') as device:
    print("Model: ", device.model)
    print("Status: ", device.status)

LICENSE: MIT

RESOURCES:

http://www.eevblog.com/forum/testgear/power-supply-ps3005d-ka3005d-rs232-protocol/
http://www.eevblog.com/forum/testgear/korad-ka3005p-io-commands/
http://sigrok.org/wiki/Velleman_PS3005D
https://gist.github.com/k-nowicki/5379272


"""

from enum import Enum
from time import sleep
import serial
from serial.serialutil import SerialException
from serial.tools import list_ports

__all__ = ['KoradSerial', 'ChannelMode', 'OnOffState', 'Tracking', 'OutputPair', 'DisconnectedError',
           'CommunicationError']

OutputPair = tuple[float, float]


class DisconnectedError(Exception):
  pass


CommunicationError = (ValueError, IndexError)



class ChannelMode(Enum):
  """ Represents channel modes.

  These values should correspond to the values returned
  by the ``STATUS?`` command.
  """
  constant_current = 0
  constant_voltage = 1


OnOffState = bool


class Tracking(Enum):
  """ Tracking state for a multichannel power supply.

  These values should correspond to the values returned
  by the ``STATUS?`` command.

  There seems to be conflicting information about these values.

  The other values I've seen are:
  *   0 - independent
  *   1 - series
  *   2 - parallel
  *   3 - symmetric

  However, I don't have a multichannel power supply to test these.
  """
  independent = 0
  series = 1
  parallel = 3


class Status(object):
  """ Decode the KoradSerial status byte.

  It appears that the firmware is a little wonky here.

  SOURCE:

  Taken from http://www.eevblog.com/forum/testgear/korad-ka3005p-io-commands/

  Contents 8 bits in the following format
      Bit   Item      Description
      0     CH1       0=CC mode, 1=CV mode
      1     CH2       0=CC mode, 1=CV mode
      2, 3  Tracking  00=Independent,01=Tracking series,11=Tracking parallel
      4     Beep      0=Off, 1=On
      5     Lock      0=Lock, 1=Unlock
      6     Output    0=Off, 1=On
      7     N/A       N/A
  """

  def __init__(self, status):
    """ Initialize object with a KoradSerial status character.

    :param status: Status value
    :type status: int
    """
    super(Status, self).__init__()
    self.raw = status
    self.channel1 = ChannelMode(status & 1)
    self.channel2 = ChannelMode((status >> 1) & 1)
    self.tracking = Tracking((status >> 2) & 3)
    self.beep = OnOffState((status >> 4) & 1)
    self.ocp = OnOffState((status >> 5) & 1)
    self.output = OnOffState((status >> 6) & 1)
    self.ovp = OnOffState((status >> 7) & 1)

  def __repr__(self):
    return "{0}".format(hex(self.raw))

  def __str__(self):
    message = "Channel 1: {0}, Channel 2: {1}, Tracking: {2}, " \
              "OVP: {3}, OCP: {4}, Beep: {5}, Output: {6}"
    return message.format(
      self.channel1.name,
      self.channel2.name,
      self.tracking.name,
      int(self.ovp),
      int(self.ocp),
      int(self.beep),
      int(self.output),
    )

  def __unicode__(self):
    return self.__str__()


def float_or_none(value):
  try:
    return float(value)
  except (TypeError, ValueError):
    return None


class KoradSerial(object):
  """
  Wrapper for communicating with a programmable KoradSerial KA3xxxxP
  power supply as a serial interface.
  """

  class Channel(object):
    """ Wrap a channel. """

    def __init__(self, serial_, channel_number):
      """

      :type serial_: KoradSerial.Serial
      :type channel_number: int
      """
      super(KoradSerial.Channel, self).__init__()
      self.__serial = serial_
      self.number = channel_number

    @property
    def current(self):
      result = self.__serial.send_receive(
        "ISET{0}?".format(self.number), fixed_length=6
      )
      # There's a bug that return a 6th character of previous output.
      # This has to be read and discarded
      # otherwise it will be prepended to the next output
      return float_or_none(result[:5])

    @current.setter
    def current(self, value):
      self.__serial.send("ISET{0}:{1:05.3f}".format(self.number, value))

    @property
    def voltage(self):
      return float_or_none(
        self.__serial.send_receive(
          "VSET{0}?".format(self.number), fixed_length=5
        )
      )

    @voltage.setter
    def voltage(self, value):
      self.__serial.send("VSET{0}:{1:05.2f}".format(self.number, value))

    @property
    def output_current(self):
      """ Retrieve this channel's current output.

      :return: Amperes
      :rtype: float or None
      """
      result = self.__serial.send_receive(
        "IOUT{0}?".format(self.number), fixed_length=5
      )
      return float_or_none(result)

    @property
    def output_voltage(self):
      """ Retrieve this channel's voltage output.

      :return: Volts
      :rtype: float or None
      """
      result = self.__serial.send_receive(
        "VOUT{0}?".format(self.number), fixed_length=5
      )
      return float_or_none(result)

    @property
    def output_pair(self) -> OutputPair:
      result = self.__serial.send_receive(
        "VOUT{0}?\rIOUT{0}?".format(self.number), fixed_length=11)

      vs, cs = result.split("\n")
      voltage = float_or_none(vs)
      current = float_or_none(cs)
      return voltage, current

  class Memory(object):
    """ Wrap a memory setting. """

    def __init__(self, serial_, memory_number):
      super(KoradSerial.Memory, self).__init__()
      self.__serial = serial_
      self.number = memory_number

    def recall(self):
      """ Recall this memory's settings.  """
      self.__serial.send("RCL{0}".format(self.number))

    def save(self):
      """ Save the current voltage and current to this memory. """
      self.__serial.send("SAV{0}".format(self.number))

  class OnOffButton(object):
    """ Wrap an off/off button. """

    def __init__(self, serial_, on_command, off_command):
      super(KoradSerial.OnOffButton, self).__init__()
      self.__serial = serial_
      self._on = on_command
      self._off = off_command

    def set(self, state: bool):
      if state:
        self.on()
      else:
        self.off()

    def on(self):
      self.__serial.send(self._on)

    def off(self):
      self.__serial.send(self._off)

  class Serial(object):
    """ Serial operations.

    There are some quirky things in communication. They go here.
    """

    def __init__(self, port: str, debug=False):
      super(KoradSerial.Serial, self).__init__()

      self.debug = debug
      self.port = serial.Serial(port, 9600, timeout=1)

    def read_byte(self):
      c = self.port.read(1)
      if self.debug:
        if len(c) > 0:
          print("read: {0}".format(hex(c[0])))
        else:
          print("read: timeout")
      return c[0]

    def read_character(self):
      try:
        c = self.port.read(1).decode('ascii')
      except UnicodeDecodeError:
        raise ValueError()
      if self.debug:
        if len(c) > 0:
          print("read: {0} = '{1}'".format(ord(c), c))
        else:
          print("read: timeout")
      return c

    def read_string(self, fixed_length=None):
      """ Read a string.

      It appears that the KoradSerial PSU returns
      zero-terminated strings.

      :return: str
      """
      result = []
      c = self.read_character()
      while len(c) > 0 and ord(c) != 0:
        result.append(c)
        if fixed_length is not None and len(result) == fixed_length:
          break
        c = self.read_character()

      if result[0] == "\n":
        result.pop(0)
      if result[-1] == "\n":
        result.pop(-1)

      return ''.join(result)

    def send(self, text):
      if self.debug:
        print("_send: ", text)
      sleep(0.01)
      try:
        self.port.write(b"\r" + text.encode('ascii'))
      except SerialException:
        raise DisconnectedError()

    def send_receive(self, text, fixed_length=None):
      self.send(text)
      return self.read_string(fixed_length)

  def __init__(self, port, debug=False):
    super(KoradSerial, self).__init__()

    self.__serial = KoradSerial.Serial(port, debug)

    # Channels: adjust voltage and current,
    # discover current output voltage.
    self.channels = [
      KoradSerial.Channel(self.__serial, i) for i in range(1, 3)
    ]

    # Memory recall/save buttons 1 through 5
    self.memories = [
      KoradSerial.Memory(self.__serial, i) for i in range(1, 6)
    ]

    # Second column buttons
    self.beep = KoradSerial.OnOffButton(self.__serial, "BEEP1", "BEEP0")
    self.lock = KoradSerial.OnOffButton(self.__serial, "LOCK1", "LOCK0")
    self.output = KoradSerial.OnOffButton(self.__serial, "OUT1", "OUT0")
    self.ocp = KoradSerial.OnOffButton(
      self.__serial, "OCP1", "OCP0"
    )
    self.ovp = KoradSerial.OnOffButton(
      self.__serial, "OVP1", "OVP0"
    )

  @staticmethod
  def scan_devices(vid: int, pid: int):
    ports = list_ports.comports()
    return [port.device for port in ports if port.vid == vid and port.pid == pid]

  def __enter__(self):
    """ See documentation for Python's ``with`` command.list_ports
    """
    return self

  def __exit__(self, _type, value, traceback):
    """ See documentation for Python's ``with`` command.
    """
    self.close()
    return False

  # ##################################################################
  # Serial operations
  # ##################################################################

  @property
  def is_open(self):
    """ Report whether the serial port is open.
    :rtype: bool
    """
    return self.__serial.port.isOpen()

  def close(self):
    """ Close the serial port """
    self.__serial.port.close()

  def open(self):
    """ Open the serial port """
    self.__serial.port.open()

  # ##################################################################
  # Power supply operations
  # ##################################################################

  @property
  def model(self):
    """ Report the power supply model information.

    :rtype: str
    """
    return self.__serial.send_receive("*IDN?")

  @property
  def status(self) -> Status:
    """ Report the power supply status.

    :rtype: KoradSerial.Status or None
    """
    self.__serial.send("STATUS?")

    status = self.__serial.read_byte()
    return Status(status)

  def track(self, value):
    """ Set tracking mode.

    This does nothing on single-channel power supply.

    :param value: Tracking mode to set.
    :type value: Tracking
    """
    translate = {
      Tracking.independent: "TRACK0",
      Tracking.series: "TRACK1",
      Tracking.parallel: "TRACK2",
    }
    if value in translate:
      self.__serial.send(translate[value])
