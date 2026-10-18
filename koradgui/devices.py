"""USB discovery and family-specific backend selection; no active probing."""
from dataclasses import dataclass
from enum import Enum

from serial.tools import list_ports


class Family(Enum):
  KORAD = 'Korad'
  FNIRSI = 'FNIRSI'

  @property
  def prefix(self):
    return 'K ' if self is Family.KORAD else 'F '

  @property
  def color(self):
    return (1.0, 0.85, 0.15, 1.0) if self is Family.KORAD else (1.0, 0.25, 0.25, 1.0)


USB_FAMILIES = {(0x0416, 0x5011): Family.KORAD, (0x2E3C, 0x5740): Family.FNIRSI}


@dataclass(frozen=True)
class Device:
  port: str
  family: Family
  serial_number: str | None = None
  location: str | None = None

  @property
  def identity(self):
    return self.family, self.serial_number or self.location or self.port

  @property
  def label(self):
    return self.port.removeprefix('/dev/tty')


def scan_devices():
  devices = []
  for port in list_ports.comports():
    if family := USB_FAMILIES.get((port.vid, port.pid)):
      devices.append(Device(port.device, family, port.serial_number, port.location))
  return sorted(devices, key=lambda device: device.port)


def selection_after_refresh(previous, devices):
  if previous is not None:
    # Prefer the exact port when otherwise identical USB identifiers are present.
    for i, device in enumerate(devices):
      if device.identity == previous.identity and device.port == previous.port:
        return i
    for i, device in enumerate(devices):
      if device.identity == previous.identity:
        return i
  return 0 if devices else -1


def make_backend(device):
  from .backends import KoradBackend, FnirsiBackend
  backend = KoradBackend if device.family is Family.KORAD else FnirsiBackend
  return backend(device.port)
