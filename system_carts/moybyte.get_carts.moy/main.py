# Get Carts is presented by the shell as a responsive system process (the app
# API, docs/app_api_v1.md). This small cart body is the recovery fallback for an
# older shell that does not know that surface yet.


def _draw():
    cls(col("white"))
    print("GET CARTS", 20, 20, col("black"))
    print("UPDATE MOYBYTE TO OPEN", 20, 40, col("dark_grey"))
    rect(144, 70, 32, 60, col("green"))
    for i in range(24):
        line(160 - i, 130 + i, 160 + i, 130 + i, col("green"))
    rect(100, 170, 120, 10, col("dark_grey"))
