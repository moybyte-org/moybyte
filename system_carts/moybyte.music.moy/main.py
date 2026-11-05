# Music is presented by the shell as a responsive system process (the app API,
# docs/app_api_v1.md). This small cart body is the recovery fallback for an
# older shell that does not know that surface yet.


def _draw():
    cls(col("dark_blue"))
    print("MUSIC", 20, 20, col("white"))
    print("UPDATE MOYBYTE TO OPEN", 20, 40, col("light_grey"))
