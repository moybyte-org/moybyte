from moybyte_sim.pygame_backend import PALETTE, sprite_color_index


def test_sprite_color_index_is_stable_and_visible():
    first = sprite_color_index("player")
    second = sprite_color_index("player")

    assert first == second
    assert 2 <= first < len(PALETTE)
