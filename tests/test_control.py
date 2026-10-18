from dataclasses import replace
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from koradgui.backends import KoradBackend, ProtocolError, Settings
from koradgui.control import PowerSupplyCtrl, Event, CmdOption
from koradgui.devices import Family


class FakeBackend:
  family = Family.FNIRSI

  def __init__(self):
    self.settings = Settings(voltage=0.42, current=0.67, ocp_value=2, ovp_value=10)
    self.calls = []
    self.failure = False
    self.failed_poll = False

  def connect(self):
    self.calls.append('connect')
    return self.settings

  def poll_measurements(self):
    if self.failed_poll:
      raise ProtocolError('unplugged')
    return []

  def read_settings(self):
    self.calls.append('read')
    return self.settings

  def set_setpoints(self, voltage, current):
    self.calls.append(('setpoints', voltage, current))
    self.settings = replace(self.settings, voltage=voltage, current=current)

  def set_status(self, output, ocp, ovp):
    self.calls.append(('output', output))
    self.settings = replace(self.settings, output=output)

  def set_lock(self, locked):
    self.calls.append(('lock', locked))

  def set_protection(self, name, value):
    self.calls.append(('protection', name, value))
    if self.failure:
      raise ProtocolError('readback differs')
    self.settings = replace(self.settings, **{name + '_value': value})
    return self.settings

  def shutdown(self, send_off):
    self.calls.append(('shutdown', send_off))


class ControllerTests(TestCase):
  def make_controller(self):
    backend = FakeBackend()
    ctrl = PowerSupplyCtrl(backend)
    self.addCleanup(ctrl.close)
    ctrl.start()
    self.assertEqual(ctrl._event_queue.get(timeout=1)[0], Event.READY)
    return ctrl, backend

  def test_ordered_setpoints_before_output_and_no_cleanup_rewrite(self):
    ctrl, backend = self.make_controller()
    ctrl.voltage, ctrl.current, ctrl.output = 1.2, 0.5, True
    ctrl.write_all()
    self.assertEqual(ctrl._event_queue.get(timeout=1)[0], Event.STATE_UPDATED)
    self.assertLess(backend.calls.index(('setpoints', 1.2, 0.5)), backend.calls.index(('output', True)))
    ctrl.close()
    self.assertEqual(backend.calls[-1], ('shutdown', True))
    self.assertEqual(sum(isinstance(c, tuple) and c[0] == 'setpoints' for c in backend.calls), 1)

  def test_failed_threshold_refreshes_actual_state_and_reports_error(self):
    ctrl, backend = self.make_controller()
    backend.failure = True
    ctrl.write_protection('ocp', 3)
    event, message = ctrl._event_queue.get(timeout=1)
    self.assertEqual(event, Event.ERROR)
    self.assertIn('readback differs', message)
    self.assertEqual(ctrl._event_queue.get(timeout=1)[0], Event.PROTECTION_FINISHED)
    self.assertEqual(ctrl.settings.ocp_value, 2)
    self.assertEqual(backend.calls.count(('protection', 'ocp', 3)), 1)
    self.assertIn('read', backend.calls)

  def test_unplug_stops_sampling_and_reports_single_disconnect(self):
    ctrl, backend = self.make_controller()
    backend.failed_poll = True
    ctrl.stream_output = True
    self.assertEqual(ctrl._event_queue.get(timeout=1), (Event.DISCONNECTED, 'unplugged'))
    ctrl.close(send_off=False)
    self.assertEqual(backend.calls[-1], ('shutdown', False))
    self.assertFalse(ctrl.ready)
    self.assertIsNone(ctrl.read_event())
    self.assertEqual(ctrl.read_output_data(), [])

  def test_start_failure_closes_backend_without_output_retry(self):
    backend = FakeBackend()
    backend.connect = Mock(side_effect=ProtocolError('wrong model'))
    ctrl = PowerSupplyCtrl(backend)
    ctrl.start()
    self.assertEqual(ctrl._event_queue.get(timeout=1), (Event.DISCONNECTED, 'wrong model'))
    ctrl.close(send_off=False)
    self.assertEqual(backend.calls, [('shutdown', False)])

  def test_output_only_does_not_write_protections_or_setpoints(self):
    ctrl, backend = self.make_controller()
    ctrl.output = False
    ctrl.write_custom({CmdOption.STATUS})
    self.assertEqual(ctrl._event_queue.get(timeout=1)[0], Event.STATE_UPDATED)
    self.assertEqual(backend.calls, ['connect', ('output', False)])


class KoradRegressionTests(TestCase):
  def test_original_initialization_and_command_delegation(self):
    calls = []
    class Channel:
      @property
      def voltage(self):
        calls.append('VSET1?')
        return 3.3
      @voltage.setter
      def voltage(self, value):
        calls.append(('VSET1', value))
      @property
      def current(self):
        calls.append('ISET1?')
        return 0.5
      @current.setter
      def current(self, value):
        calls.append(('ISET1', value))
      output_pair = (3.3, 0.4)
    class Korad:
      channels = [Channel()]
      def __init__(self):
        for name in ('lock', 'ocp', 'ovp', 'output'):
          setattr(self, name, SimpleNamespace(set=lambda value, name=name: calls.append((name, value))))
      @property
      def status(self):
        calls.append('STATUS?')
        return SimpleNamespace(output=False, ocp=False, ovp=False)
      def close(self):
        calls.append('close')
    backend = KoradBackend('/dev/fake', factory=lambda port: Korad())
    settings = backend.connect()
    self.assertEqual(calls, [('lock', True), ('ocp', False), ('ovp', False),
                            ('output', False), 'STATUS?', 'VSET1?', 'ISET1?'])
    self.assertEqual(settings.max_current, 5.1)
    backend.set_setpoints(5, 1)
    backend.set_status(True, True, False)
    self.assertEqual(calls[-5:], [('VSET1', 5), ('ISET1', 1), ('ocp', True), ('ovp', False), ('output', True)])
    self.assertEqual(backend.poll_measurements()[0][1:], (3.3, 0.4))
    backend.shutdown(True)
    self.assertEqual(calls[-3:], [('output', False), ('lock', False), 'close'])
