# Paint is presented by the shell as a responsive system process (the app API,
# docs/app_api_v1.md). This small cart body is the recovery fallback for an
# older shell that does not know that surface yet.


def _draw():
    cls(col("white"))
    print("PAINT", 20, 20, col("black"))
    print("UPDATE MOYBYTE TO OPEN", 20, 40, col("dark_grey"))
    rect(20, 60, 200, 140, col("light_grey"))
    rect(28, 68, 184, 124, col("white"))
    for i in range(5):
        rect(232, 60 + i * 28, 24, 20,
             (col("red"), col("orange"), col("green"), col("blue"),
              col("pink"))[i])
