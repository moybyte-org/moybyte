// moy_lock: the kernel's lock for a state outside the kernel that tasks of
// their own write as well as the VM's: the app ABI's seqlock writer
// (native/moy_app/moy_app.c), whose writes the shell makes on the VM's task
// and a compiled program's clipboard write on its own. The seqlock takes one
// writer at a time; this makes that so. A mutex, never a critical section: a
// clipboard write copies up to 4 KiB, longer than moy_sync.h's lock is for.
// Never in an ISR, never nested. The browser has one thread and no lock.

#ifndef MOY_LOCK_H
#define MOY_LOCK_H

void moy_lock_take(void);
void moy_lock_give(void);

#endif // MOY_LOCK_H
