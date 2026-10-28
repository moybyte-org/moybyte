# Storybook is presented by the shell as a responsive system process. This
# small cart body is the recovery fallback for an older shell that does not
# know that surface yet.


def _draw():
    cls(col("dark_blue"))
    print("STORYBOOK", 20, 20, col("white"))
    print("UPDATE MOYBYTE TO OPEN", 20, 40, col("light_grey"))
    rect(60, 70, 96, 130, col("white"))
    rect(164, 70, 96, 130, col("light_grey"))
    rect(156, 70, 8, 130, col("brown"))
    for i in range(6):
        line(70, 88 + i * 18, 146, 88 + i * 18, col("dark_grey"))
