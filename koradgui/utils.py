import numpy as np
from imgui_bundle import imgui, ImVec2


def set_text_pos_center(text, offset):
  area_width = imgui.get_content_region_avail()[0]
  text_width = imgui.calc_text_size(text)[0]
  imgui.set_cursor_pos_x(offset + area_width / 2 - text_width / 2)


def is_in_area(val, tl: ImVec2, br: ImVec2):
  return tl.x <= val.x < br.x and tl.y <= val.y < br.y


class ScrollingBuffer:
  def __init__(self, maxlen: int):
    self._max_length: int = maxlen
    self.length = 0
    self.offset = 0

    self.timestamps = np.zeros(shape=(maxlen,), dtype=float)
    self.values = np.zeros(shape=(maxlen,), dtype=float)

  @property
  def last_value(self):
    return self.values[(self.offset - 1) % self._max_length]

  def append(self, timestamp: float, val: float):
    self.timestamps[self.offset] = timestamp
    self.values[self.offset] = val
    self.offset = (self.offset + 1) % self._max_length
    if self.length < self._max_length:
      self.length += 1
