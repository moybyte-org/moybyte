# Writer is presented by the shell as a responsive system process. This small
# cart body is the recovery fallback for an older shell that does not know that
# surface yet.


def _draw():
    cls(col("white"))
    print("WRITER", 20, 20, col("black"))
    print("UPDATE MOYBYTE TO OPEN", 20, 40, col("dark_grey"))
    rect(14, 56, 2, 168, col("pink"))
    for i in range(9):
        line(20, 76 + i * 18, 300, 76 + i * 18, col("light_grey"))
