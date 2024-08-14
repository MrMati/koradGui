from imgui_bundle import imgui, ImVec2


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
