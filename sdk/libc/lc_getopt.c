// getopt and getopt_long (sdk/README.md), POSIX's and GNU's: options end at
// the first operand or at "--"; a leading ':' in the option string makes a
// missing argument ':' and silences the messages.
#include "lc.h"

#include <getopt.h>
#include <string.h>
#include <unistd.h>

LC_API char *optarg;
LC_API int optind = 1, opterr = 1, optopt;
#if defined(LC_NEWLIB)
LC_API int optreset;
#endif

static int s_at;    // the next option letter's place in argv[optind], 0 between words

static void say(const char *argv0, const char *what, int c, const char *name) {
    if (!opterr) {
        return;
    }
    if (name != NULL) {
        fprintf(stderr, "%s: %s '--%s'\n", argv0, what, name);
    } else {
        fprintf(stderr, "%s: %s -- '%c'\n", argv0, what, c);
    }
}

static void step_word(void) {
    optind++;
    s_at = 0;
}

static int long_opt(int argc, char *const argv[], const struct option *longs, int *index,
                    int colon) {
    const char *word = argv[optind] + 2;
    const char *eq = strchr(word, '=');
    size_t n = eq != NULL ? (size_t)(eq - word) : strlen(word);
    const struct option *hit = NULL;
    int hits = 0;
    for (int i = 0; longs[i].name != NULL; i++) {
        if (strncmp(longs[i].name, word, n) != 0) {
            continue;
        }
        if (strlen(longs[i].name) == n) {
            hit = &longs[i];
            hits = 1;
            break;
        }
        hit = &longs[i];
        hits++;
    }
    step_word();
    if (hits != 1) {
        say(argv[0], hits ? "option is ambiguous" : "unrecognized option", 0, word);
        optopt = 0;
        return '?';
    }
    if (index != NULL) {
        *index = (int)(hit - longs);
    }
    optarg = NULL;
    if (hit->has_arg == no_argument && eq != NULL) {
        say(argv[0], "option doesn't allow an argument", 0, hit->name);
        optopt = hit->val;
        return '?';
    }
    if (hit->has_arg != no_argument) {
        if (eq != NULL) {
            optarg = (char *)eq + 1;
        } else if (hit->has_arg == required_argument) {
            if (optind >= argc) {
                say(argv[0], "option requires an argument", 0, hit->name);
                optopt = hit->val;
                return colon ? ':' : '?';
            }
            optarg = argv[optind++];
        }
    }
    if (hit->flag != NULL) {
        *hit->flag = hit->val;
        return 0;
    }
    return hit->val;
}

static int next(int argc, char *const argv[], const char *opts, const struct option *longs,
                int *index) {
    if (optind == 0
#if defined(LC_NEWLIB)
        || optreset
#endif
    ) {
        optind = 1;
        s_at = 0;
#if defined(LC_NEWLIB)
        optreset = 0;
#endif
    }
    if (*opts == '+' || *opts == '-') {
        opts++;
    }
    int colon = *opts == ':';
    if (s_at == 0) {
        const char *w = optind < argc ? argv[optind] : NULL;
        if (w == NULL || w[0] != '-' || w[1] == '\0') {
            return -1;
        }
        if (w[1] == '-' && w[2] == '\0') {
            optind++;
            return -1;
        }
        if (w[1] == '-' && longs != NULL) {
            return long_opt(argc, argv, longs, index, colon);
        }
        s_at = 1;
    }
    const char *w = argv[optind];
    int c = (unsigned char)w[s_at++];
    const char *spec = c != ':' ? strchr(opts, c) : NULL;
    int last = w[s_at] == '\0';
    optarg = NULL;
    if (spec == NULL) {
        optopt = c;
        say(argv[0], "invalid option", c, NULL);
        if (last) {
            step_word();
        }
        return '?';
    }
    if (spec[1] != ':') {
        if (last) {
            step_word();
        }
        return c;
    }
    if (!last) {
        optarg = (char *)w + s_at;
        step_word();
    } else if (spec[2] == ':') {
        step_word();
    } else if (optind + 1 < argc) {
        optarg = argv[optind + 1];
        optind += 2;
        s_at = 0;
    } else {
        step_word();
        optopt = c;
        if (colon) {
            return ':';
        }
        say(argv[0], "option requires an argument", c, NULL);
        return '?';
    }
    return c;
}

LC_API int getopt(int argc, char *const argv[], const char *opts) {
    return next(argc, argv, opts, NULL, NULL);
}

LC_API int getopt_long(int argc, char *const argv[], const char *opts,
                       const struct option *longs, int *index) {
    return next(argc, argv, opts, longs, index);
}
