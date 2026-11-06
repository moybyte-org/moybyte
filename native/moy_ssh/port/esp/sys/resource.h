// The resource limits Dropbear sets (no core dump): ESP-IDF has none.
#ifndef MOY_SSH_SYS_RESOURCE_H
#define MOY_SSH_SYS_RESOURCE_H
#define RLIMIT_CORE 4
struct rlimit {
    long rlim_cur, rlim_max;
};
static inline int getrlimit(int r, struct rlimit *l) {
    (void)r;
    l->rlim_cur = l->rlim_max = 0;
    return 0;
}
static inline int setrlimit(int r, const struct rlimit *l) {
    (void)r;
    (void)l;
    return 0;
}
#endif
