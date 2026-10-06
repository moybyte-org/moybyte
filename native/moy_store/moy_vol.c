// The volume seam's three backends (moy_vol.h has the contract).

#include <string.h>

#include "moy_vol.h"

#if MOY_VOL_FAT
#ifndef FFCONF_H
#define FFCONF_H "lib/oofatfs/ffconf.h"
#endif
#include "lib/oofatfs/ff.h"
// An ESP-IDF image links IDF's own fatfs beside oofatfs, with the same global
// names and other signatures, and a strong reference from here would pull its
// f_open in first. There the calls are WEAK references: they pull nothing, and
// bind to the f_ symbols VfsFat already brings in, oofatfs's.
#if MOY_VOL_FAT_WEAK
extern __typeof__(f_open) f_open __attribute__((weak));
extern __typeof__(f_read) f_read __attribute__((weak));
extern __typeof__(f_write) f_write __attribute__((weak));
extern __typeof__(f_close) f_close __attribute__((weak));
extern __typeof__(f_stat) f_stat __attribute__((weak));
extern __typeof__(f_opendir) f_opendir __attribute__((weak));
extern __typeof__(f_readdir) f_readdir __attribute__((weak));
extern __typeof__(f_closedir) f_closedir __attribute__((weak));
extern __typeof__(f_mkdir) f_mkdir __attribute__((weak));
extern __typeof__(f_unlink) f_unlink __attribute__((weak));
extern __typeof__(f_rename) f_rename __attribute__((weak));
#endif
#define FF(fn) fn
#endif
#if MOY_VOL_LFS2
#include "lib/littlefs/lfs2.h"
#endif
#if MOY_VOL_POSIX
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

struct moy_vol_file {
    struct moy_vol_file *next;      // the open list moy_vol_unwind walks
    size_t bytes;                   // as allocated
    uint8_t kind;
    void *fs;
    union {
        int fd;
        #if MOY_VOL_FAT
        FIL fil;
        #endif
        #if MOY_VOL_LFS2
        lfs2_file_t lf;
        #endif
    } u;
    #if MOY_VOL_LFS2
    struct lfs2_file_config lcfg;   // its buffer follows the struct
    #endif
};

static moy_vol_file_t *open_files;

static void unlink_open(moy_vol_file_t *f) {
    for (moy_vol_file_t **p = &open_files; *p; p = &(*p)->next) {
        if (*p == f) {
            *p = f->next;
            return;
        }
    }
}

// -- FAT ----------------------------------------------------------------------

#if MOY_VOL_FAT
// fresult_to_errno_table's values (extmod/vfs_fat.c), indexed by FRESULT.
static const uint8_t fat_errno[] = {
    0, MOY_EIO, MOY_EIO, MOY_EBUSY, 2, 2, MOY_EINVAL, MOY_EACCES, MOY_EEXIST,
    MOY_EINVAL, MOY_EROFS, MOY_ENODEV, MOY_ENODEV, MOY_ENODEV, MOY_EIO,
    MOY_EIO, MOY_EIO, MOY_ENOMEM, MOY_EMFILE, MOY_EINVAL,
};

static int fat_err(FRESULT r) {
    return (unsigned)r < sizeof fat_errno ? fat_errno[r] : MOY_EIO;
}

static int fat_root(const char *p) {
    return p[0] == 0 || (p[0] == '/' && p[1] == 0);
}
#endif

// -- littlefs -----------------------------------------------------------------

#if MOY_VOL_LFS2
static int lfs_err(int r) {
    return r >= 0 ? 0 : -r;
}
#endif

// -- POSIX ----------------------------------------------------------------------

#if MOY_VOL_POSIX
static int px_err(void) {
    return errno ? errno : MOY_EIO;
}
#endif

int moy_vol_open(const moy_vol_t *v, const char *path, int mode,
                 moy_vol_file_t **out) {
    size_t bytes = sizeof(moy_vol_file_t);
    #if MOY_VOL_LFS2
    if (v->kind == MOY_VOL_KIND_LFS2) {
        bytes += ((lfs2_t *)v->fs)->cfg->cache_size;
    }
    #endif
    moy_vol_file_t *f = moy_store_alloc(bytes);
    if (f == NULL) {
        return MOY_ENOMEM;
    }
    f->bytes = bytes;
    f->kind = v->kind;
    f->fs = v->fs;
    int rc = MOY_ENODEV;
    switch (v->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT:
            rc = fat_err(FF(f_open)((FATFS *)v->fs, &f->u.fil, path,
                                mode == MOY_VOL_WRITE ? FA_WRITE | FA_CREATE_ALWAYS
                                                      : FA_READ));
            break;
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2:
            f->lcfg.buffer = (uint8_t *)f + sizeof(moy_vol_file_t);
            rc = lfs_err(lfs2_file_opencfg(
                (lfs2_t *)v->fs, &f->u.lf, path,
                mode == MOY_VOL_WRITE ? LFS2_O_WRONLY | LFS2_O_CREAT | LFS2_O_TRUNC
                                      : LFS2_O_RDONLY, &f->lcfg));
            break;
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX:
            errno = 0;
            f->u.fd = mode == MOY_VOL_WRITE
                      ? open(path, O_WRONLY | O_CREAT | O_TRUNC, 0666)
                      : open(path, O_RDONLY);
            rc = f->u.fd < 0 ? px_err() : 0;
            if (rc == 0 && mode != MOY_VOL_WRITE) {
                struct stat st;
                if (fstat(f->u.fd, &st) == 0 && S_ISDIR(st.st_mode)) {
                    close(f->u.fd);
                    rc = MOY_EISDIR;
                }
            }
            break;
        #endif
        default:
            break;
    }
    if (rc != 0) {
        moy_store_free(f, bytes);
        return rc;
    }
    f->next = open_files;
    open_files = f;
    *out = f;
    return 0;
}

int moy_vol_read(moy_vol_file_t *f, void *buf, size_t n, size_t *got) {
    *got = 0;
    switch (f->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT: {
            UINT br = 0;
            FRESULT r = FF(f_read)(&f->u.fil, buf, (UINT)n, &br);
            *got = br;
            return fat_err(r);
        }
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2: {
            lfs2_ssize_t r = lfs2_file_read((lfs2_t *)f->fs, &f->u.lf, buf,
                                            (lfs2_size_t)n);
            if (r < 0) {
                return lfs_err((int)r);
            }
            *got = (size_t)r;
            return 0;
        }
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX:
            for (;;) {
                errno = 0;
                ssize_t r = read(f->u.fd, buf, n);
                if (r >= 0) {
                    *got = (size_t)r;
                    return 0;
                }
                if (errno != EINTR) {
                    return px_err();
                }
            }
        #endif
        default:
            return MOY_EBADF;
    }
}

int moy_vol_write(moy_vol_file_t *f, const void *buf, size_t n) {
    switch (f->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT: {
            UINT bw = 0;
            FRESULT r = FF(f_write)(&f->u.fil, buf, (UINT)n, &bw);
            if (r != FR_OK) {
                return fat_err(r);
            }
            return bw == n ? 0 : MOY_ENOSPC;
        }
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2: {
            lfs2_ssize_t r = lfs2_file_write((lfs2_t *)f->fs, &f->u.lf, buf,
                                             (lfs2_size_t)n);
            if (r < 0) {
                return lfs_err((int)r);
            }
            return (size_t)r == n ? 0 : MOY_ENOSPC;
        }
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX: {
            const uint8_t *p = buf;
            while (n) {
                errno = 0;
                ssize_t r = write(f->u.fd, p, n);
                if (r < 0) {
                    if (errno == EINTR) {
                        continue;
                    }
                    return px_err();
                }
                p += r;
                n -= (size_t)r;
            }
            return 0;
        }
        #endif
        default:
            return MOY_EBADF;
    }
}

int moy_vol_size(moy_vol_file_t *f, uint32_t *n) {
    switch (f->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT:
            *n = (uint32_t)f_size(&f->u.fil);
            return 0;
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2: {
            lfs2_soff_t r = lfs2_file_size((lfs2_t *)f->fs, &f->u.lf);
            if (r < 0) {
                return lfs_err((int)r);
            }
            *n = (uint32_t)r;
            return 0;
        }
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX: {
            struct stat st;
            if (fstat(f->u.fd, &st) != 0) {
                return px_err();
            }
            *n = (uint32_t)st.st_size;
            return 0;
        }
        #endif
        default:
            return MOY_EBADF;
    }
}

int moy_vol_close(moy_vol_file_t *f) {
    int rc = MOY_EBADF;
    unlink_open(f);
    switch (f->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT:
            rc = fat_err(FF(f_close)(&f->u.fil));
            break;
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2:
            rc = lfs_err(lfs2_file_close((lfs2_t *)f->fs, &f->u.lf));
            break;
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX:
            errno = 0;
            rc = close(f->u.fd) == 0 ? 0 : px_err();
            break;
        #endif
        default:
            break;
    }
    moy_store_free(f, f->bytes);
    return rc;
}

void moy_vol_unwind(void) {
    while (open_files != NULL) {
        moy_vol_close(open_files);
    }
}

int moy_vol_stat(const moy_vol_t *v, const char *path, moy_vol_stat_t *st) {
    st->is_dir = 0;
    st->size = 0;
    switch (v->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT: {
            if (fat_root(path)) {
                st->is_dir = 1;
                return 0;
            }
            FILINFO fno;
            FRESULT r = FF(f_stat)((FATFS *)v->fs, path, &fno);
            if (r != FR_OK) {
                return fat_err(r);
            }
            st->is_dir = (fno.fattrib & AM_DIR) != 0;
            st->size = (uint32_t)fno.fsize;
            return 0;
        }
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2: {
            struct lfs2_info info;
            int r = lfs2_stat((lfs2_t *)v->fs, path, &info);
            if (r < 0) {
                return lfs_err(r);
            }
            st->is_dir = info.type == LFS2_TYPE_DIR;
            st->size = info.size;
            return 0;
        }
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX: {
            struct stat s;
            errno = 0;
            if (stat(path, &s) != 0) {
                return px_err();
            }
            st->is_dir = S_ISDIR(s.st_mode) != 0;
            st->size = (uint32_t)s.st_size;
            return 0;
        }
        #endif
        default:
            return MOY_ENODEV;
    }
}

int moy_vol_list(const moy_vol_t *v, const char *dir, moy_vol_ent_fn fn,
                 void *ctx) {
    int rc = 0;
    switch (v->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT: {
            FF_DIR *d = moy_store_alloc(sizeof(FF_DIR));
            FILINFO *fno = moy_store_alloc(sizeof(FILINFO));
            if (d == NULL || fno == NULL) {
                rc = MOY_ENOMEM;
            } else {
                rc = fat_err(FF(f_opendir)((FATFS *)v->fs, d, dir));
                if (rc == 0) {
                    for (;;) {
                        FRESULT r = FF(f_readdir)(d, fno);
                        if (r != FR_OK) {
                            rc = fat_err(r);
                            break;
                        }
                        if (fno->fname[0] == 0) {
                            break;
                        }
                        rc = fn(ctx, fno->fname, strlen(fno->fname),
                                (fno->fattrib & AM_DIR) != 0, (uint32_t)fno->fsize);
                        if (rc) {
                            break;
                        }
                    }
                    FF(f_closedir)(d);
                }
            }
            if (fno != NULL) {
                moy_store_free(fno, sizeof(FILINFO));
            }
            if (d != NULL) {
                moy_store_free(d, sizeof(FF_DIR));
            }
            return rc;
        }
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2: {
            lfs2_dir_t *d = moy_store_alloc(sizeof(lfs2_dir_t));
            struct lfs2_info *info = moy_store_alloc(sizeof(struct lfs2_info));
            if (d == NULL || info == NULL) {
                rc = MOY_ENOMEM;
            } else {
                rc = lfs_err(lfs2_dir_open((lfs2_t *)v->fs, d, dir));
                if (rc == 0) {
                    for (;;) {
                        int r = lfs2_dir_read((lfs2_t *)v->fs, d, info);
                        if (r <= 0) {
                            rc = lfs_err(r);
                            break;
                        }
                        if (info->name[0] == '.' && (info->name[1] == 0
                            || (info->name[1] == '.' && info->name[2] == 0))) {
                            continue;
                        }
                        rc = fn(ctx, info->name, strlen(info->name),
                                info->type == LFS2_TYPE_DIR, info->size);
                        if (rc) {
                            break;
                        }
                    }
                    lfs2_dir_close((lfs2_t *)v->fs, d);
                }
            }
            if (info != NULL) {
                moy_store_free(info, sizeof(struct lfs2_info));
            }
            if (d != NULL) {
                moy_store_free(d, sizeof(lfs2_dir_t));
            }
            return rc;
        }
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX: {
            errno = 0;
            DIR *d = opendir(dir);
            if (d == NULL) {
                return px_err();
            }
            size_t dl = strlen(dir);
            for (;;) {
                errno = 0;
                struct dirent *e = readdir(d);
                if (e == NULL) {
                    rc = errno ? px_err() : 0;
                    break;
                }
                const char *nm = e->d_name;
                if (nm[0] == '.' && (nm[1] == 0 || (nm[1] == '.' && nm[2] == 0))) {
                    continue;
                }
                size_t nl = strlen(nm), full_n = dl + nl + 2;
                char *full = moy_store_alloc(full_n);
                if (full == NULL) {
                    rc = MOY_ENOMEM;
                    break;
                }
                memcpy(full, dir, dl);
                full[dl] = '/';
                memcpy(full + dl + 1, nm, nl);
                struct stat s;
                int ok = stat(full, &s) == 0;
                moy_store_free(full, full_n);
                rc = fn(ctx, nm, nl, ok && S_ISDIR(s.st_mode),
                        ok ? (uint32_t)s.st_size : 0u);
                if (rc) {
                    break;
                }
            }
            closedir(d);
            return rc;
        }
        #endif
        default:
            return MOY_ENODEV;
    }
}

int moy_vol_mkdir(const moy_vol_t *v, const char *path) {
    switch (v->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT:
            return fat_err(FF(f_mkdir)((FATFS *)v->fs, path));
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2:
            return lfs_err(lfs2_mkdir((lfs2_t *)v->fs, path));
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX:
            errno = 0;
            return mkdir(path, 0777) == 0 ? 0 : px_err();
        #endif
        default:
            return MOY_ENODEV;
    }
}

int moy_vol_remove(const moy_vol_t *v, const char *path) {
    switch (v->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT:
            return fat_err(FF(f_unlink)((FATFS *)v->fs, path));
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2:
            return lfs_err(lfs2_remove((lfs2_t *)v->fs, path));
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX: {
            struct stat s;
            errno = 0;
            if (stat(path, &s) == 0 && S_ISDIR(s.st_mode)) {
                return rmdir(path) == 0 ? 0 : px_err();
            }
            return unlink(path) == 0 ? 0 : px_err();
        }
        #endif
        default:
            return MOY_ENODEV;
    }
}

static int native_rename(const moy_vol_t *v, const char *src, const char *dst) {
    switch (v->kind) {
        #if MOY_VOL_FAT
        case MOY_VOL_KIND_FAT:
            return fat_err(FF(f_rename)((FATFS *)v->fs, src, dst));
        #endif
        #if MOY_VOL_LFS2
        case MOY_VOL_KIND_LFS2:
            return lfs_err(lfs2_rename((lfs2_t *)v->fs, src, dst));
        #endif
        #if MOY_VOL_POSIX
        case MOY_VOL_KIND_POSIX:
            errno = 0;
            return rename(src, dst) == 0 ? 0 : px_err();
        #endif
        default:
            return MOY_ENODEV;
    }
}

int moy_vol_rename(const moy_vol_t *v, const char *src, const char *dst) {
    if (v->kind != MOY_VOL_KIND_FAT) {
        moy_vol_stat_t st;
        if (moy_vol_stat(v, dst, &st) == 0) {
            return MOY_EEXIST;
        }
    }
    return native_rename(v, src, dst);
}

int moy_vol_replace(const moy_vol_t *v, const char *src, const char *dst) {
    int rc = native_rename(v, src, dst);
    if (rc != 0 && v->kind == MOY_VOL_KIND_FAT) {
        moy_vol_remove(v, dst);
        rc = native_rename(v, src, dst);
    }
    return rc;
}
