# My Art is presented by the shell as a responsive system process (the app
# API, docs/app_api_v1.md). This small cart body is the recovery fallback for an
# older shell that does not know that surface yet.


def _draw():
    cls(col("white"))
    print("MY ART", 20, 20, col("black"))
    print("UPDATE MOYBYTE TO OPEN", 20, 40, col("dark_grey"))
    for i in range(3):
        rect(24 + i * 96, 70, 80, 60, col("light_grey"))
        rect(28 + i * 96, 74, 72, 52, (col("blue"), col("pink"), col("green"))[i])
