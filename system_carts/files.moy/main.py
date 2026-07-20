# Files is presented by the shell as a responsive system process (the app API,
# docs/app_api_v1.md). This small cart body is the recovery fallback for an
# older shell that does not know that surface yet.


def _draw():
    cls(col("white"))
    print("FILES", 20, 20, col("black"))
    print("UPDATE MOYBYTE TO OPEN", 20, 40, col("dark_grey"))
    for i in range(6):
        x = 20 + (i % 3) * 96
        y = 70 + (i // 3) * 76
        rect(x, y, 84, 56, col("light_grey"))
        rect(x + 4, y + 4, 76, 36, col("cyan") if i % 2 else col("green"))
        print("ART " + str(i + 1), x + 4, y + 44, col("black"))
