from pathlib import Path


def test_lilygo_firmware_scaffold_exists():
    root = Path("firmware/lilygo_t_deck_plus")

    assert (root / "platformio.ini").exists()
    assert (root / "boards" / "T-Deck.json").exists()
    assert (root / "src" / "main.cpp").exists()


def test_lilygo_firmware_scaffold_uses_board_profile():
    main_cpp = Path("firmware/lilygo_t_deck_plus/src/main.cpp").read_text(encoding="utf-8")
    profile = Path("firmware/lilygo_t_deck_plus/include/kidcode_board_profile.h").read_text(
        encoding="utf-8"
    )

    assert "KidCode firmware smoke test" in main_cpp
    assert "Display: KidCode native tiny_runner canvas" in main_cpp
    assert "renderNativeTinyRunner" in main_cpp
    assert "KIDCODE_CANVAS_SIZE = 128" in main_cpp
    assert "tftFillScreen" in main_cpp
    assert "KIDCODE_BOARD_TFT_BACKLIGHT" in main_cpp
    assert "KIDCODE_BOARD_POWERON 10" in profile
    assert "lilygo_t_deck_plus" in profile
