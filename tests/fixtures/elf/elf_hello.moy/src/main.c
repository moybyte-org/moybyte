// hello: a native command's first words, through the POSIX rows alone.
#include <string.h>
#include <unistd.h>

static void put(const char *s) {
    write(1, s, strlen(s));
}

int main(int argc, char **argv) {
    put("hello from a native module");
    for (int i = 1; i < argc; i++) {
        put(" ");
        put(argv[i]);
    }
    put("\n");
    return 0;
}
