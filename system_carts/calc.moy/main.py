# Calc is presented by the shell as a responsive system process (the app API,
# docs/app_api_v1.md). This small cart body is the recovery fallback for an
# older shell that does not know that surface yet.


def _draw():
    cls(col("white"))
    print("CALC", 20, 20, col("black"))
    print("UPDATE MOYBYTE TO OPEN", 20, 40, col("dark_grey"))
    rect(20, 60, 120, 40, col("black"))
    print("1234", 100, 76, col("green"))
    for r in range(2):
        for c in range(4):
            rect(20 + c * 32, 112 + r * 32, 26, 26, col("light_grey"))
