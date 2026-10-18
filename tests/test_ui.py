from types import SimpleNamespace
import time
from unittest import TestCase
from unittest.mock import patch

from imgui_bundle import imgui, implot, ImVec2

from koradgui.backends import Settings
from koradgui.devices import Device, Family
from koradgui.ui import KoradGui
from koradgui import widgets


class GuiController:
  def __init__(self, family, ready=True):
    self.family = family
    self.ready = ready
    self.pending = not ready
    self.output = self.ocp = self.ovp = False
    self.voltage, self.current = 3.3, 0.5
    self.settings = Settings(voltage=3.3, current=0.5, ocp_value=2, ovp_value=15)
    self.samples = []
    self.writes = []

  def read_event(self):
    return None

  def read_output_data(self):
    result, self.samples = self.samples, []
    return result

  def write_custom(self, options):
    self.writes.append(options)

  def write_protection(self, name, value):
    self.writes.append((name, value))


class GuiTests(TestCase):
  def setUp(self):
    self.context = imgui.create_context()
    self.plot_context = implot.create_context()
    io = imgui.get_io()
    io.set_ini_filename('')
    io.display_size = ImVec2(800, 550)
    io.delta_time = 1 / 60
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    font = io.fonts.add_font_default()
    self.scan = patch('koradgui.ui.scan_devices', return_value=[])
    self.scan.start()
    self.gui = KoradGui()
    self.gui.big_font = self.gui.bigger_font = font
    self.positions = {}
    self.protection_rects = []
    self.buttons = []
    self.pickers = []
    self.input_actions = {}

  def tearDown(self):
    self.scan.stop()
    implot.destroy_context(self.plot_context)
    imgui.destroy_context(self.context)

  def frame(self):
    self.positions = {}
    self.protection_rects = []
    self.buttons = []
    self.pickers = []
    original_button = widgets.text_sized_button
    original_spin = widgets.SpinBox.draw
    original_checkbox = imgui.checkbox
    original_subplot = implot.begin_subplots
    original_switch = widgets.switch_button
    original_input = imgui.input_float
    def record_button(text, *args, **kwargs):
      result = original_button(text, *args, **kwargs)
      self.positions['output' if text in ('ON', 'OFF') else 'connection'] = tuple(imgui.get_item_rect_min())
      return result
    def record_spin(widget):
      self.positions[widget.unit] = tuple(imgui.get_cursor_pos())
      return original_spin(widget)
    def record_checkbox(label, *args, **kwargs):
      self.positions[label] = tuple(imgui.get_cursor_pos())
      return original_checkbox(label, *args, **kwargs)
    def record_subplot(*args, **kwargs):
      self.positions['graphs'] = tuple(imgui.get_cursor_pos())
      return original_subplot(*args, **kwargs)
    def record_switch(label, *args, **kwargs):
      self.buttons.append(label)
      return original_switch(label, *args, **kwargs)
    def record_input(label, *args, **kwargs):
      self.pickers.append(label)
      result = original_input(label, *args, **kwargs)
      self.protection_rects.append((tuple(imgui.get_item_rect_min()), tuple(imgui.get_item_rect_max())))
      return self.input_actions.pop(label, result)
    imgui.new_frame()
    imgui.set_next_window_pos(ImVec2(0, 0))
    imgui.set_next_window_size(ImVec2(800, 550))
    imgui.begin('Layout smoke', flags=imgui.WindowFlags_.no_decoration | imgui.WindowFlags_.no_saved_settings)
    try:
      with patch.object(widgets, 'text_sized_button', side_effect=record_button), \
           patch.object(widgets.SpinBox, 'draw', new=record_spin), \
           patch.object(imgui, 'checkbox', side_effect=record_checkbox), \
           patch.object(implot, 'begin_subplots', side_effect=record_subplot), \
           patch.object(widgets, 'switch_button', side_effect=record_switch), \
           patch.object(imgui, 'input_float', side_effect=record_input):
        self.gui.app()
    finally:
      imgui.end()
      imgui.render()
    return dict(self.positions)

  def test_all_three_states_keep_other_controls_stationary(self):
    self.frame()  # settle the existing custom SpinBox's measured widths
    empty = self.frame()
    self.assertEqual(self.buttons + self.pickers, [])
    self.gui.ctrl = GuiController(Family.KORAD, ready=False)
    self.assertEqual(self.frame(), empty)
    self.assertEqual(self.buttons + self.pickers, [])
    self.gui.ctrl.ready = True
    self.gui.ctrl.pending = False
    self.assertEqual(self.frame(), empty)
    self.assertEqual(self.buttons, ['OCP', 'OVP'])
    self.assertEqual(self.pickers, [])
    self.gui.ctrl = GuiController(Family.FNIRSI)
    self.assertEqual(self.frame(), empty)
    self.assertEqual(self.buttons, [])
    self.assertEqual(self.pickers, ['##ocp_threshold', '##ovp_threshold'])
    for (left, top), (right, bottom) in self.protection_rects:
      self.assertGreaterEqual(left, 550)
      self.assertLessEqual(right, 790)
      self.assertLess(bottom, 100)  # fit above the unchanged setpoint row
    self.gui.ctrl = None
    self.assertEqual(self.frame(), empty)
    self.assertEqual(self.buttons + self.pickers, [])

  def test_graphs_consume_multiple_fresh_samples(self):
    self.gui.ctrl = GuiController(Family.FNIRSI)
    now = time.monotonic()
    self.gui.ctrl.samples = [(now, 1, 0.1), (now + 0.1, 2, 0.2)]
    self.frame()
    self.assertEqual(self.gui.voltage_buffer.length, 2)
    self.assertEqual(self.gui.voltage_buffer.last_value, 2)
    self.assertEqual(self.gui.current_buffer.last_value, 0.2)

  def test_fnirsi_does_not_run_korad_delayed_switch_logic(self):
    self.gui.ctrl = GuiController(Family.FNIRSI)
    self.gui.output_last_set = time.time() - 10
    self.frame()
    self.assertEqual(self.gui.ctrl.writes, [])
    self.assertEqual(self.gui.output_last_set, -1)

  def test_selector_preview_draws_family_prefix_separately(self):
    device = Device('/dev/ttyACM0', Family.FNIRSI, 'serial')
    self.gui.ports = [device]
    self.gui.sel_port_idx = 0
    self.gui.last_scan = time.monotonic()
    with patch.object(self.gui, 'draw_device_label', wraps=self.gui.draw_device_label) as draw:
      self.frame()
      self.assertEqual(draw.call_args.args[0], device)
    self.assertEqual(device.family.prefix, 'F ')
    self.assertNotEqual(Family.FNIRSI.color, Family.KORAD.color)

  def test_threshold_commit_and_invalid_edit(self):
    self.gui.ctrl = GuiController(Family.FNIRSI)
    self.gui.protection_values = {'ocp': 2, 'ovp': 15}
    self.input_actions['##ocp_threshold'] = (True, 3.0)
    self.frame()
    self.assertEqual(self.gui.ctrl.writes, [('ocp', 3.0)])
    self.frame()
    self.assertEqual(self.gui.ctrl.writes, [('ocp', 3.0)])
    for invalid in (0.0, -1.0, 100.0, float('nan'), float('inf')):
      self.input_actions['##ovp_threshold'] = (True, invalid)
      self.frame()
      self.assertEqual(self.gui.protection_values['ovp'], 15)
      self.assertIn('OVP must be', self.gui.error)
    self.assertEqual(self.gui.ctrl.writes, [('ocp', 3.0)])
