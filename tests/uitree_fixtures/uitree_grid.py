"""A page placed by a grid (`ui.grid`): its room in by pad=, cut into rows
then columns with gap= between, a button a cell and `span=` cells along its
row; a button hidden by its condition leaves its cells empty.
Each press counts; Seven hides the wide key."""
import uitree as ui

s = ui.state(n=0, wide=True)


def press():
    s.n += 1


def seven():
    s.wide = not s.wide


def opened():
    s.n = 0
    s.wide = True


ui.hook("opened", opened)
ui.action("one", "1", press)
ui.action("two", "2", press)
ui.action("three", "3", press)
ui.action("seven", "7", seven)
ui.action("zero", "0 ({n})", press)
ui.action("dot", ".", press)


def build():
    with ui.page("Grid"):
        with ui.grid(3, 3, gap="m", pad="s"):
            ui.button("one")
            ui.button("two")
            ui.button("three")
            ui.button("seven")
            ui.button("zero", span=2, shown="wide")
            ui.button("dot")


def layer(engine):
    import uitree_app
    return uitree_app.app_layer("grid_" + ("c" if "C" in engine.__name__ else "ref"), "Grid fixture", build,
                                engine=engine)
