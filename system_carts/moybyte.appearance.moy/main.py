# Appearance is presented by the shell as a responsive system process. This small
# cart body is the recovery fallback for an older shell that does not know that
# surface yet.


def _draw():
    cls(col("dark_blue"))
    print("APPEARANCE", 16, 32, col("white"))
    print("UPDATE MOYBYTE TO OPEN", 16, 58, col("light_grey"))
    rect(16, 88, 80, 58, col("dark_purple"))
    rect(112, 88, 80, 58, col("blue"))
    rect(208, 88, 80, 58, col("pink"))
