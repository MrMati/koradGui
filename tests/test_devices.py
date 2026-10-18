from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from koradgui.devices import Device, Family, scan_devices, selection_after_refresh, make_backend
from koradgui.backends import KoradBackend, FnirsiBackend


class DiscoveryTests(TestCase):
  def test_identification_and_sorting_without_probing(self):
    ports = [SimpleNamespace(device='/dev/ttyACM1', vid=0x2e3c, pid=0x5740,
                             serial_number='05E2311C1E04', location='1-4.3'),
             SimpleNamespace(device='/dev/ttyACM0', vid=0x0416, pid=0x5011,
                             serial_number='0018281D0458', location='5-1'),
             SimpleNamespace(device='/dev/ttyUSB0', vid=0x1234, pid=0x5740,
                             serial_number=None, location=None)]
    with patch('koradgui.devices.list_ports.comports', return_value=ports):
      devices = scan_devices()
    self.assertEqual([d.family for d in devices], [Family.KORAD, Family.FNIRSI])
    self.assertEqual([d.label for d in devices], ['ACM0', 'ACM1'])
    self.assertEqual([d.family.prefix for d in devices], ['K ', 'F '])
    self.assertIsInstance(make_backend(devices[0]), KoradBackend)
    self.assertIsInstance(make_backend(devices[1]), FnirsiBackend)

  def test_selection_survives_reordering_and_port_renumbering(self):
    old = Device('/dev/ttyACM0', Family.FNIRSI, 'serial')
    other = Device('/dev/ttyACM0', Family.KORAD, 'other')
    new = Device('/dev/ttyACM1', Family.FNIRSI, 'serial')
    self.assertEqual(selection_after_refresh(old, [other, new]), 1)
    self.assertEqual(selection_after_refresh(old, [other]), 0)
    self.assertEqual(selection_after_refresh(old, []), -1)
