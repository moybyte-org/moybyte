// moy_sh_g.h: a shell session's state, every writable global dash had.
//
// dash keeps its state in globals; a session keeps it here, in ONE struct
// allocated from the session's arena (moy_sh_arena.h) in PSRAM, so a board
// spends no .bss on the shell and several sessions (the screen, the serial
// face) each hold their own. The patch (patches/moybyte.patch) turns every
// definition and extern of a dash global into a macro naming its field,
// `#define exitstatus (moy_sh_g->exitstatus)`; an aggregate (a struct dash
// embedded) is a pointer here and `(*moy_sh_g->name)` there, allocated with
// the session (moy_sh.c, sh_state_new). `moy_sh_g` is the running session's.
//
// Because the arena holds ALL of a session's state, a subshell is a copy of
// it: the fork the shell asks for snapshots the arena, runs the child on the
// same stack and puts the bytes back when it exits (moy_sh.c, "fork").
//
// The array sizes are dash's (ATABSIZE, CMDTABLESIZE, VTABSIZE, NOPTS);
// moy_sh.c asserts each against dash's own definition.

#ifndef MOY_SH_G_H
#define MOY_SH_G_H

#include <limits.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>
#include <wchar.h>

struct alias;
struct arglist;
struct heredoc_s;
struct ifsregion;
struct job;
struct jmploc;
struct localvar_list;
struct nodelist;
struct output;
struct parsefile_s;
struct redirtab;
struct shparam;
struct stack_block;
struct stdin_state_s;
struct t_op;
struct tblentry;
struct var;
union node;
union yystype;
struct moy_sh_obj;

#define MOY_SH_FDS 20                 // a process's file descriptors
#define MOY_SH_G_ATAB 39
#define MOY_SH_G_CMDTAB 31
#define MOY_SH_G_VTAB 39
#define MOY_SH_G_NOPTS 18

// A process's own state beside dash's: the descriptor table and the working
// folder. It lives in the arena with the rest, so a child's dup2s and cds
// vanish with the child.
struct moy_sh_proc {
    struct moy_sh_obj *fd[MOY_SH_FDS];
    unsigned char cloexec[MOY_SH_FDS];
    char *cwd;                          // absolute, no trailing slash but "/"
    int pid;                            // this process's (the session's, or a child's)
};

struct moy_sh_globals {
    struct moy_sh *sess;                // the session this state is
    // alias.c
    struct alias *atab[MOY_SH_G_ATAB];
    // arith_yacc.c, arith_yylex.c
    const char *arith_startbuf;
    const char *arith_buf;
    union yystype *yylval;
    int last_token;
    // cd.c
    char *curdir;
    char *physdir;
    // error.c
    struct jmploc *handler;
    int exception;
    int suppressint;
    volatile sig_atomic_t intpending;
    int errlinno;
    // eval.c
    int evalskip;
    int skipcount;
    int loopnest;
    int funcline;
    char *commandname;
    int exitstatus;
    int back_exitstatus;
    int savestatus;
    int inps4;
    int tpip[2];
    // exec.c
    struct tblentry *cmdtable[MOY_SH_G_CMDTAB];
    int builtinloc;
    struct tblentry **lastcmdentry;
    const char *pathopt;
    // expand.c
    char *expdest;
    struct nodelist *argbackq;
    struct ifsregion *ifsfirst;
    struct ifsregion *ifslastp;
    struct arglist *exparg;
    char ifsmap[128];
    const char *ncifs;
    size_t ifsmb0len;
    wchar_t *wcifs;
    // input.c
    struct parsefile_s *basepf;
    char *basebuf;
    struct parsefile_s *toppf;
    struct stdin_state_s *stdin_state;
    struct parsefile_s *parsefile;
    int whichprompt;
    int stdin_istty;
    // jobs.c
    struct job *jobtab;
    unsigned njobs;
    pid_t backgndpid;
    int initialpgrp;
    int ttyfd;
    struct job *curjob;
    int vforked;
    int jobctl;
    int job_warning;
    char *cmdnextc;
    // main.c
    int rootpid;
    int mypid;
    int shlvl;
    struct jmploc *main_handler;
    // memalloc.c
    struct stack_block *stackbase;
    struct stack_block *stackp;
    char *stacknxt;
    size_t stacknleft;
    char *sstrend;
    // mystring.c
    char nullstr[1];
    // nodes.c
    int funcblocksize;
    int funcstringsize;
    void *funcblock;
    char *funcstring;
    // options.c
    char *arg0;
    struct shparam *shellparam;
    char **argptr;
    char *optionarg;
    char *optptr;
    char *minusc;
    char optlist[MOY_SH_G_NOPTS];
    // output.c
    struct output *outputf;
    struct output *errout;
    struct output *preverrout;
    struct output *out1;
    struct output *out2;
    // parser.c
    struct heredoc_s *heredoclist;
    int doprompt;
    int needprompt;
    int lasttoken;
    int tokpushback;
    char *wordtext;
    int checkkwd;
    struct nodelist *backquotelist;
    union node *redirnode;
    struct heredoc_s *heredoc;
    int quoteflag;
    // bltin/printf.c
    int rval;
    char **gargv;
    // redir.c
    struct redirtab *redirlist;
    unsigned closed_redirs;
    // bltin/test.c
    char **t_wp;
    const struct t_op *t_wp_op;
    // trap.c
    char *trap[NSIG];
    int ptrap;
    int trapcnt;
    char sigmode[NSIG - 1];
    char gotsig[NSIG - 1];
    volatile sig_atomic_t pending_sig;
    volatile sig_atomic_t gotsigchld;
    int is_interactive;
    // var.c
    struct localvar_list *localvar_stack;
    char defifsvar[8];
    char defoptindvar[9];
    int lineno;
    char linenovar[sizeof("LINENO=") + sizeof(int) * CHAR_BIT / 3 + 1];
    struct var *vartab[MOY_SH_G_VTAB];
    struct var *varinit;

    // the face's input: more may follow (the run is parsing what a face sent)
    // and the parser read to its end
    int input_more;
    int input_eof;

    // the process
    struct moy_sh_proc proc;
};

// Thread-local (moy_sh.h, TASKS). A board links one image, so its access is
// local-exec: the RISC-V toolchain's default for an extern reaches it
// through a GOT the IDF's linker script discards.
#ifdef ESP_PLATFORM
#define MOY_SH_TLS __thread __attribute__((tls_model("local-exec")))
#else
#define MOY_SH_TLS __thread
#endif
extern MOY_SH_TLS struct moy_sh_globals *moy_sh_g;

#endif // MOY_SH_G_H
