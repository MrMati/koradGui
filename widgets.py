from imgui_bundle import imgui, ImVec2

from utils import is_in_area


def text_sized_button(text, size_text, center=False, offset=0):
  window_width = imgui.get_window_width()
  text_size = imgui.calc_text_size(size_text)
  button_width = text_size.x + imgui.get_style().frame_padding.x * 2
  button_height = text_size.y + imgui.get_style().frame_padding.y * 2
  if center:
    button_x = (window_width - button_width) * 0.5 + offset
  else:
    button_x = imgui.get_cursor_pos_x() + offset
  imgui.set_cursor_pos_x(button_x)
  return imgui.button(text, ImVec2(button_width, button_height))


def switch_button(text, v, size: ImVec2 = ImVec2(0, 0)):
  imgui.push_style_var(imgui.StyleVar_.alpha, 10.75 if v else 0.75)
  nv = imgui.button(text, size)
  imgui.pop_style_var()

  return nv


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
