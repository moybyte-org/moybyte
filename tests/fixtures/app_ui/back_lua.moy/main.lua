-- Back that steps out of an app's own views before it leaves the app
-- (docs/app_api_v1.md section 4, "Back steps out of your views first"), as
-- a Lua app: back_py.moy is the same app in Python, and tests/test_app_ui.py
-- holds both to the same steps. Its first page opens a second page, and the
-- second a popover; while the second page shows, the app lends the bar its
-- way back (ui.bar's `back`). Into pmem: 0 the page (1 or 2), 1 whether the
-- popover shows, 2 how many times the bar's way back reached the app.

page = 1
pop = false
ups = 0

function _layout(w, h, fs) end

function _draw()
  if ui.bar(page == 2 and "Details" or "", {}, {}, page == 2) == -1 then
    page = 1
    ups = ups + 1
  end
  ui.page(page == 2 and "Details" or "Back")
  if page == 1 then
    if ui.button("Details", "primary") then page = 2 end
  else
    if ui.button("More", "primary") then pop = true end
    if pop then
      pop = ui.popover("more", 120)
      ui.label("In the popover")
      ui.popover()
    end
  end
  pmem(0, page)
  pmem(1, pop and 1 or 0)
  pmem(2, ups)
end
