from kidcode.sprites import Sprite


def test_sprite_touching():
    a = Sprite("a", x=0, y=0, w=8, h=8)
    b = Sprite("b", x=7, y=7, w=8, h=8)
    c = Sprite("c", x=20, y=20, w=8, h=8)

    assert a.touching(b)
    assert not a.touching(c)


def test_invisible_sprite_does_not_touch():
    a = Sprite("a", x=0, y=0, w=8, h=8)
    b = Sprite("b", x=0, y=0, w=8, h=8)
    b.visible = False

    assert not a.touching(b)
