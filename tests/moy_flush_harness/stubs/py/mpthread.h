#ifndef H_STUB_PY_MPTHREAD_H
#define H_STUB_PY_MPTHREAD_H

void h_gil_exit(void);
void h_gil_enter(void);

// drain() is the one place the engine releases the GIL. The harness counts the
// pair so an unbalanced exit -- which on a board is a VM that never runs again
// -- is a failed scenario rather than an invisible one.
#define MP_THREAD_GIL_EXIT()  h_gil_exit()
#define MP_THREAD_GIL_ENTER() h_gil_enter()

#endif // H_STUB_PY_MPTHREAD_H
