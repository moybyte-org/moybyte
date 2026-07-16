# Sheets is presented by the shell as a responsive system process (the app API,
# docs/app_api_v1.md). This small cart body is the recovery fallback for an older
# shell that does not know that surface yet.


def _draw():
    cls(col("white"))
    print("SHEETS", 20, 20, col("black"))
    print("UPDATE MOYBYTE TO OPEN", 20, 40, col("dark_grey"))
    for r in range(4):
        for c in range(4):
            rect(20 + c * 40, 70 + r * 26, 38, 24, col("light_grey"))
            rectb(20 + c * 40, 70 + r * 26, 38, 24, col("dark_grey"))
