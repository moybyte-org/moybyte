// moy_alloc: the off-heap buffers' C entries (modmoy_alloc.c).

#ifndef MOY_ALLOC_H
#define MOY_ALLOC_H

// A VM's end swept every buffer it left behind.
void moy_alloc_vm_swept(void);

#endif // MOY_ALLOC_H
