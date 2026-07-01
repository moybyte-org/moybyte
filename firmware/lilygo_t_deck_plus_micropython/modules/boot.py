try:
    from tdeck_board import init_board_pins

    init_board_pins()
except Exception as exc:
    print("Moybyte boot power setup skipped:", exc)

print("Moybyte MicroPython spike boot")
