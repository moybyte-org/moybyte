# The Terminal is presented by the shell as a responsive system process (the
# app API, docs/app_api_v1.md): the C shell's screen face. This small cart
# body is the recovery fallback for an older shell that does not know it yet.


def _draw():
    cls(col("black"))
    print("TERMINAL", 20, 20, col("white"))
    print("UPDATE MOYBYTE TO OPEN", 20, 40, col("light_grey"))
    print("$ _", 20, 70, col("green"))
