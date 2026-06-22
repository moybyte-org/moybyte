try:
    from tdeck_board import init_board_pins

    init_board_pins()
except Exception as exc:
    print("KidCode boot power setup skipped:", exc)

print("KidCode MicroPython spike boot")
