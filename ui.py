from typing import Optional, Any

import numpy as np
from imgui_bundle import immapp, implot, hello_imgui, imgui, ImVec2, ImVec4

from control import PowerSupplyCtrl, Event
from koradserial import KoradSerial
from utils import is_in_area, ScrollingBuffer
import widgets


class SpinBox:
  def __init__(self, unit: str, digits: int, fract_places: int, max_value: float):
    self.unit = unit
    self.n = digits
    self.digits = [0] * digits
    self.fract_places = fract_places
    self.max_value = max_value
    self._width = 0.0

  @property
  def width(self):
    return self._width

  @property
  def value(self):
    num = float(''.join([str(d) for d in self.digits]))
    return num / (10 ** self.fract_places)

  @value.setter
  def value(self, v: float):
    v = min(v, self.max_value)
    fmt = f"{{:0{self.n + 1}.{self.fract_places}f}}"
    s = fmt.format(v)
    s = s.replace('.', '')
    self.digits = [int(c) for c in s]

  # TODO: limit max value
  def increment(self, i):
    if i < 0:
      return False

    if self.digits[i] < 9:
      self.digits[i] += 1
      return True

    self.digits[i] = 0
    return self.increment(i - 1)

  def decrement(self, i):
    if i < 0:
      return False

    if self.digits[i] > 0:
      self.digits[i] -= 1
      return True

    if i == 0:
      return False

    if sum(self.digits[0:i]) == 0:
      return False

    self.digits[i] = 9
    self.decrement(i - 1)
    return True

  def draw(self):
    widget_pos = imgui.get_cursor_pos()
    digit_size = imgui.calc_text_size("0")
    dot_width = imgui.calc_text_size(".")[0]
    spacing = imgui.get_font_size() * 0.06
    self._width = self.n * (digit_size.x + spacing) + dot_width + imgui.calc_text_size(" " + self.unit).x

    dot_offset = 0.0
    draw_list = imgui.get_window_draw_list()

    changed = False

    for i, digit in enumerate(self.digits):
      digit_pos = ImVec2(widget_pos.x + i * (digit_size.x + spacing) + dot_offset, widget_pos.y)
      imgui.set_cursor_pos(digit_pos)
      imgui.text(str(digit))

      if i + 1 == self.n - self.fract_places:
        imgui.set_cursor_pos(
          ImVec2(widget_pos.x + i * (digit_size.x + spacing) + digit_size.x + spacing / 2, widget_pos.y))
        imgui.text(".")
        dot_offset = dot_width

      if imgui.get_current_context().current_item_flags & imgui.internal.ItemFlags_.disabled.value:
        continue

      mouse_pos = imgui.get_mouse_pos()
      left_click = imgui.is_mouse_clicked(imgui.MouseButton_.left)  # type: ignore
      right_click = imgui.is_mouse_clicked(imgui.MouseButton_.right)  # type: ignore
      digit_end = digit_pos + digit_size
      if is_in_area(mouse_pos, digit_pos, digit_end):
        if right_click:
          changed = digit != 0
          self.digits[i] = 0
          continue

        height_rel = (mouse_pos.y - digit_pos.y) / digit_size.y
        down = 0.6
        if height_rel < 1 - down:
          draw_list.add_rect_filled(digit_pos, digit_end - ImVec2(0, digit_size.y * down), 0x500000ff)  # red

          if left_click:
            changed |= self.increment(i)
        elif height_rel > down:
          draw_list.add_rect_filled(digit_pos + ImVec2(0, digit_size.y * down), digit_end, 0x50ff0000)  # blue

          if left_click:
            changed |= self.decrement(i)

    imgui.set_cursor_pos(ImVec2(widget_pos.x + self.n * (digit_size.x + spacing) + dot_offset, widget_pos.y))
    imgui.text(" " + self.unit)

    return changed


class KoradGui:
  def __init__(self):
    self.ctrl: Optional[PowerSupplyCtrl] = None
    self.ports = KoradSerial.scan_devices(0x0416, 0x5011)
    self.sel_port_idx = 0 if self.ports else -1
    self.auto_set = False
    self.ocp_auto_set = False
    self.ovp_auto_set = False

    self.big_font: Optional[imgui.ImFont] = None
    self.bigger_font: Optional[imgui.ImFont] = None
    self.mid_offset = 40

    self.voltage_input = SpinBox("V", 4, 2, 30.0)
    self.current_input = SpinBox("A", 4, 3, 5.1)

    self.time = 0
    self.graph_zoom = 10
    self.voltage_buffer = ScrollingBuffer(16 * 30)
    self.current_buffer = ScrollingBuffer(16 * 30)

  def connect_ui(self):
    imgui.set_cursor_pos(ImVec2(10, 10))
    imgui.set_next_item_width(110)

    imgui.begin_disabled(self.ctrl is not None)
    combo_preview_value = self.ports[self.sel_port_idx] if self.ports else "NONE"
    if imgui.begin_combo("##com_port_sel", combo_preview_value):
      self.ports = KoradSerial.scan_devices(0x0416, 0x5011)
      for idx, port in enumerate(self.ports):
        is_selected = self.sel_port_idx == idx
        if imgui.selectable(port, is_selected):
          self.sel_port_idx = idx
        if is_selected:
          imgui.set_item_default_focus()
      imgui.end_combo()
    imgui.end_disabled()

    imgui.set_cursor_pos(ImVec2(140, 10))

    if self.ctrl is None:
      imgui.begin_disabled(self.sel_port_idx == -1)
      if widgets.text_sized_button("CONNECT", "DISCONNECT"):
        self.device_connect()
      imgui.end_disabled()
    else:
      if widgets.text_sized_button("DISCONNECT", "DISCONNECT"):
        self.device_disconnect()

  def options_ui(self):
    wnd_width = imgui.get_window_width()
    imgui.push_font(self.big_font)
    imgui.set_cursor_pos(ImVec2(0, 10))
    output = self.ctrl.output if self.connected else False
    if widgets.text_sized_button("OFF" if output else "ON", "OFF", center=True, offset=self.mid_offset):
      self.ctrl.output = not output
      self.ctrl.write_all()

    imgui.push_style_var(imgui.StyleVar_.disabled_alpha, 1.0 if self.connected else 0.6)

    imgui.set_cursor_pos(ImVec2(wnd_width / 2 + 160, 10))
    if widgets.switch_button("OCP", self.ocp_auto_set):
      self.ocp_auto_set = not self.ocp_auto_set

    imgui.set_cursor_pos(ImVec2(wnd_width / 2 + 270, 10))
    if widgets.switch_button("OVP", self.ovp_auto_set):
      self.ovp_auto_set = not self.ovp_auto_set

    imgui.pop_style_var()

    imgui.pop_font()

    imgui.set_cursor_pos(ImVec2(imgui.get_window_size()[0] / 2 - 40 + self.mid_offset, 100))
    auto_changed, self.auto_set = imgui.checkbox("AUTO", self.auto_set)
    if auto_changed:
      self.ctrl.write_setpoint()

  def inputs_ui(self):
    imgui.push_font(self.bigger_font)
    wnd_width = imgui.get_window_width()
    imgui.set_cursor_pos(ImVec2(wnd_width / 8 + self.mid_offset, 100))
    changed = False
    if self.voltage_input.draw() and self.connected:
      changed |= True
      self.ctrl.voltage = self.voltage_input.value

    imgui.set_cursor_pos(ImVec2(wnd_width - self.current_input.width - wnd_width / 8 + self.mid_offset, 100))
    if self.current_input.draw() and self.connected:
      changed |= True
      self.ctrl.current = self.current_input.value

    if self.auto_set and changed:
      self.ctrl.write_setpoint()

    imgui.pop_font()

  def presets_ui(self):
    ...

  def graphs_ui(self):
    def graph(setpoint: float, buffer: ScrollingBuffer, fmt: str = "5.2f", min_y: float = 0):
      if implot.begin_plot("", flags=implot.Flags_.canvas_only):
        implot.setup_axes("", "", implot.AxisFlags_.no_tick_labels)

        max_y = setpoint * 1.2
        implot.setup_axes_limits(self.time - self.graph_zoom, self.time, min_y, max_y, implot.Cond_.always)
        ticks = list(dict.fromkeys([0, round(max_y / 2, 1), setpoint]))  # remove duplicates
        ticks = [format(tick, fmt).ljust(6) for tick in ticks]
        if len(ticks) == 1:
          ticks.append(ticks[0])  # bugfix
        implot.setup_axis_ticks(
          implot.ImAxis_.y1, 0.0, setpoint, len(ticks) if self.connected else 0, ticks, False)

        if self.connected and self.ctrl.output:
          implot.tag_y(buffer.last_value, ImVec4(0, 1, 1, 5), format(buffer.last_value, fmt))

        implot.plot_line("", buffer.timestamps, buffer.values, 0, buffer.offset)
        implot.end_plot()

    if self.connected:
      self.time += imgui.get_io().delta_time
      # self.voltage_buffer.append(self.t, np.sin(self.t))
      if data := self.ctrl.read_output_data():
        assert (len(data) == 1)  # It seems there is always one data point
        self.voltage_buffer.append(self.time, data[0][0])
        self.current_buffer.append(self.time, data[0][1])

    imgui.set_cursor_pos(ImVec2(20, 200))
    if implot.begin_subplots("Outputs", 1, 2, ImVec2(-1, -1), flags=implot.SubplotFlags_.no_title):
      graph(self.voltage_input.value, self.voltage_buffer, min_y=-0.1)
      graph(self.current_input.value, self.current_buffer, "5.3f", -0.01)
      implot.end_subplots()

    if imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled):
      if wheeld := imgui.get_io().mouse_wheel:
        self.graph_zoom -= wheeld
        self.graph_zoom = min(30.0, max(1.0, self.graph_zoom))

  def app(self):
    if self.connected and (event := self.ctrl.read_event()):
      self.callback(*event)

    self.connect_ui()

    imgui.push_style_var(imgui.StyleVar_.disabled_alpha, 1.0 if self.connected else 0.6)  # type: ignore
    imgui.begin_disabled(not self.connected or self.ctrl.pending)

    self.options_ui()
    self.inputs_ui()
    self.presets_ui()
    self.graphs_ui()

    imgui.end_disabled()
    imgui.pop_style_var(1)

  def callback(self, event: Event, data: Any):
    if event is Event.READ_FINISHED:
      self.voltage_input.value = self.ctrl.voltage
      self.current_input.value = self.ctrl.current
    elif event is Event.DISCONNECTED:
      self.device_disconnect()

  @property
  def connected(self) -> bool:
    return self.ctrl is not None

  def device_connect(self):
    self.ctrl = PowerSupplyCtrl(None if 0 else KoradSerial(self.ports[self.sel_port_idx]))
    self.ctrl.start()
    self.ctrl.lock = True
    self.ctrl.write_lock()
    self.ctrl.read_settings()
    self.ctrl.stream_output = True

  def device_disconnect(self):
    if self.connected:
      self.ctrl.lock = False
      self.ctrl.output = False
      self.ctrl.write_all()
      self.ctrl.close()
      self.ctrl = None

  def start(self):
    runner_params = hello_imgui.RunnerParams()
    runner_params.app_window_params.window_geometry.size = (800, 550)
    runner_params.fps_idling.enable_idling = False
    runner_params.callbacks.show_gui = self.app
    runner_params.callbacks.before_exit = self.device_disconnect

    def font_load():
      robot_path = "assets/ttf/roboto/Roboto-Medium.ttf"
      imgui.get_io().fonts.add_font_from_file_ttf(robot_path, 24)  # default font
      self.big_font = imgui.get_io().fonts.add_font_from_file_ttf(robot_path, 40)
      self.bigger_font = imgui.get_io().fonts.add_font_from_file_ttf(robot_path, 60)

    runner_params.callbacks.load_additional_fonts = font_load

    immapp.run(runner_params, immapp.AddOnsParams(with_implot=True))
